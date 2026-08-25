from __future__ import annotations

from dataclasses import replace
import plistlib
from types import SimpleNamespace
from xml.etree import ElementTree

from app.audio_features import CueHint, TrackFeatures
from app.rekordbox_sync import (
    _beat_loop_size,
    _cue_kind,
    _downsample_waveform,
    _ensure_content,
    _get_or_add_artist,
    _rekordbox_cue_out_seconds,
    _rekordbox_waveform_lane,
    _snap_loop_hints_to_rekordbox_grid,
    _sync_cue_hints,
    list_usb_devices,
    repair_generated_active_loops,
    repair_generated_off_grid_loops,
    remove_generated_cues_from_playlist,
    write_rekordbox_xml,
)


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def one(self):
        if len(self.rows) != 1:
            raise AssertionError(f"expected one row, got {len(self.rows)}")
        return self.rows[0]


class FakeDb:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.added = []
        self.deleted = []
        self.next_id = 1000
        self.flushed = 0

    def get_cue(self, **kwargs):
        assert kwargs["ContentID"] == "track-1"
        return FakeQuery(self.existing)

    def get_content(self, **kwargs):
        return FakeQuery([])

    def get_menu_items(self, **kwargs):
        assert kwargs["Name"] == "TRACK"
        return FakeQuery([SimpleNamespace(rb_local_usn=10)])

    def get_device(self):
        return FakeQuery([SimpleNamespace(ID="device-1", MasterDBID="master-1")])

    def get_artist(self, **kwargs):
        return FakeQuery([])

    def generate_unused_id(self, table, is_28_bit=True, id_field_name="ID"):
        self.next_id += 1
        return self.next_id

    def add(self, instance):
        self.added.append(instance)

    def delete(self, instance):
        self.deleted.append(instance)

    def flush(self):
        self.flushed += 1


class ActiveLoopQuery:
    def __init__(self, rows):
        self.rows = rows

    def join(self, *_args):
        return self

    def filter(self, *_args):
        return self

    def all(self):
        return self.rows


class ActiveLoopDb:
    def __init__(self, rows):
        self.rows = rows
        self.committed = False
        self.closed = False
        self.rolled_back = False
        self.session = SimpleNamespace(rollback=self._rollback)

    def query(self, *_args):
        return ActiveLoopQuery(self.rows)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True

    def _rollback(self):
        self.rolled_back = True


class CueRemovalDb:
    def __init__(self, contents, cues):
        self.contents = contents
        self.cues = cues
        self.playlist = SimpleNamespace(ID="playlist-1", Name="Set", is_folder=False)
        self.deleted = []
        self.committed = False
        self.closed = False
        self.rolled_back = False
        self.session = SimpleNamespace(rollback=self._rollback)

    def get_playlist(self, **kwargs):
        return self.playlist if kwargs.get("ID") == self.playlist.ID else None

    def get_playlist_songs(self, **kwargs):
        assert kwargs["PlaylistID"] == self.playlist.ID
        return FakeQuery([SimpleNamespace(ContentID=content_id) for content_id in self.contents])

    def get_content(self, **kwargs):
        content = self.contents.get(str(kwargs["ID"]))
        return content

    def get_cue(self, **kwargs):
        return FakeQuery(self.cues.get(str(kwargs["ContentID"]), []))

    def delete(self, instance):
        self.deleted.append(instance)

    def flush(self):
        pass

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True

    def _rollback(self):
        self.rolled_back = True


def test_cue_kind_maps_hotcue_slots_to_rekordbox_kinds():
    assert _cue_kind(CueHint("A", 1.0, "hot", 0)) == 1
    assert _cue_kind(CueHint("C", 1.0, "hot", 2)) == 3
    assert _cue_kind(CueHint("D", 1.0, "hot", 3)) == 5
    assert _cue_kind(CueHint("E", 1.0, "hot", 4)) == 6
    assert _cue_kind(CueHint("Memory", 1.0, "memory", None)) == 0
    assert _cue_kind(CueHint("Bad", 1.0, "hot", 8)) is None
    assert _cue_kind(CueHint("Intro Loop", 1.0, "loop", 3, 5.0, 8)) == 5


def test_ensure_content_creates_rekordbox_content_with_string_ids(tmp_path):
    path = tmp_path / "track.mp3"
    path.write_bytes(b"mp3")
    db = FakeDb()
    features = replace(_features([]), path=str(path))

    content, created = _ensure_content(db, features)

    assert created is True
    assert isinstance(content.ID, str)
    assert isinstance(content.MasterSongID, str)
    assert isinstance(content.rb_file_id, str)
    assert content.ID == content.MasterSongID
    assert content.FolderPath == str(path.resolve())
    assert db.flushed == 1


def test_get_or_add_artist_creates_artist_with_string_id():
    db = FakeDb()

    artist = _get_or_add_artist(db, "Artist")

    assert isinstance(artist.ID, str)
    assert artist.Name == "Artist"
    assert db.flushed == 1


def test_loop_hints_snap_to_rekordbox_grid_with_exact_beat_length():
    features = _features(
        [
            CueHint("Intro", 0.07, "hot", 0),
            CueHint("Exit Loop", 10.19, "loop", 4, 13.96, 8),
            CueHint("Outro", 40.0, "memory", None),
        ]
    )
    grid = [70, 539, 1008, 1477, 1945, 2414, 2883, 3352, 3820, 4289, 4758, 5227, 5695, 6164, 6633, 7102, 7570, 8039, 8508, 8977, 9445, 9914, 10383, 10852, 11320, 11789, 12258, 12727, 13195, 13664, 14133]

    snapped = _snap_loop_hints_to_rekordbox_grid(features, grid)
    by_name = {cue.name: cue for cue in snapped.cue_hints}

    assert by_name["Intro"].seconds == 0.07
    assert by_name["Outro"].seconds == 40.0
    assert by_name["Exit Loop"].seconds == 10.383
    assert by_name["Exit Loop"].end_seconds == 14.133


def test_loop_hints_keep_audio_grid_when_rekordbox_grid_is_unavailable():
    features = _features([CueHint("Exit Loop", 10.19, "loop", 4, 13.96, 8)])

    assert _snap_loop_hints_to_rekordbox_grid(features, []).cue_hints == features.cue_hints


def test_repair_generated_active_loops_only_disables_soundcloud_dl_auto_loops(monkeypatch, tmp_path):
    from app import rekordbox_sync

    generated_cue = SimpleNamespace(
        ID="cue-1",
        Kind=5,
        InMsec=180000,
        OutMsec=184000,
        ActiveLoop=1,
        Comment="Exit Loop",
    )
    generated_content = SimpleNamespace(
        ID="track-1",
        Title="Generated Track",
        FolderPath="/tmp/generated.mp3",
        Commnt="soundcloud-dl | https://soundcloud.com/a/b",
        Artist=SimpleNamespace(Name="Artist"),
    )
    manual_cue = SimpleNamespace(
        ID="cue-2",
        Kind=5,
        InMsec=120000,
        OutMsec=124000,
        ActiveLoop=1,
        Comment="Manual Loop",
    )
    manual_content = SimpleNamespace(
        ID="track-2",
        Title="Manual Track",
        FolderPath="/tmp/manual.mp3",
        Commnt="",
        Artist=None,
    )
    db = ActiveLoopDb([(generated_cue, generated_content), (manual_cue, manual_content)])
    backup = tmp_path / "backup"

    monkeypatch.setattr(rekordbox_sync, "Rekordbox6Database", lambda: db)
    monkeypatch.setattr(rekordbox_sync, "rekordbox_running", lambda: False)
    monkeypatch.setattr(rekordbox_sync, "backup_rekordbox", lambda: backup)

    result = repair_generated_active_loops()

    assert generated_cue.ActiveLoop == 0
    assert manual_cue.ActiveLoop == 1
    assert [issue.title for issue in result.repaired] == ["Generated Track"]
    assert result.backup_dir == backup
    assert db.committed is True
    assert db.closed is True


def test_repair_generated_off_grid_loops_snaps_only_managed_four_or_eight_beat_loops(monkeypatch, tmp_path):
    from app import rekordbox_sync

    generated_cue = SimpleNamespace(
        ID="cue-1",
        Kind=6,
        InMsec=10190,
        OutMsec=13960,
        BeatLoopSize=(8 << 16) | 1,
        ActiveLoop=0,
        Comment="Exit Loop",
    )
    generated_content = SimpleNamespace(
        ID="track-1",
        Title="Generated Track",
        FolderPath="/tmp/generated.mp3",
        Commnt="soundcloud-dl | https://soundcloud.com/a/b",
        CueUpdated="4",
        Artist=SimpleNamespace(Name="Artist"),
    )
    manual_cue = SimpleNamespace(
        ID="cue-2",
        Kind=6,
        InMsec=10190,
        OutMsec=13960,
        BeatLoopSize=(8 << 16) | 1,
        ActiveLoop=0,
        Comment="Exit Loop",
    )
    manual_content = SimpleNamespace(
        ID="track-2",
        Title="Manual Track",
        FolderPath="/tmp/manual.mp3",
        Commnt="",
        CueUpdated="2",
        Artist=None,
    )
    db = ActiveLoopDb([(generated_cue, generated_content), (manual_cue, manual_content)])
    backup = tmp_path / "backup"
    grid = [70 + index * 469 for index in range(40)]

    monkeypatch.setattr(rekordbox_sync, "Rekordbox6Database", lambda: db)
    monkeypatch.setattr(rekordbox_sync, "rekordbox_running", lambda: False)
    monkeypatch.setattr(rekordbox_sync, "backup_rekordbox", lambda: backup)
    monkeypatch.setattr(rekordbox_sync, "_rekordbox_grid_times_ms", lambda _content: grid)

    result = repair_generated_off_grid_loops()

    assert generated_cue.InMsec == 10388
    assert generated_cue.OutMsec == 14140
    assert generated_cue.ActiveLoop == 0
    assert generated_content.CueUpdated == "5"
    assert manual_cue.InMsec == 10190
    assert manual_content.CueUpdated == "2"
    assert [issue.title for issue in result.repaired] == ["Generated Track"]
    assert result.backup_dir == backup
    assert db.committed is True
    assert db.closed is True


def test_remove_generated_cues_from_playlist_preserves_manual_and_external_cues(monkeypatch, tmp_path):
    from app import rekordbox_sync

    managed = SimpleNamespace(
        ID="track-1",
        Commnt="soundcloud-dl | https://soundcloud.com/a/b",
        CueUpdated="4",
    )
    external = SimpleNamespace(ID="track-2", Commnt="", CueUpdated="2")
    generated = SimpleNamespace(Comment="Intro")
    generated_loop = SimpleNamespace(Comment="Exit Loop")
    manual = SimpleNamespace(Comment="My Cue")
    external_named_like_generated = SimpleNamespace(Comment="Intro")
    db = CueRemovalDb(
        {"track-1": managed, "track-2": external},
        {
            "track-1": [generated, generated_loop, manual],
            "track-2": [external_named_like_generated],
        },
    )
    backup = tmp_path / "backup"

    monkeypatch.setattr(rekordbox_sync, "Rekordbox6Database", lambda: db)
    monkeypatch.setattr(rekordbox_sync, "rekordbox_running", lambda: False)
    monkeypatch.setattr(rekordbox_sync, "backup_rekordbox", lambda: backup)

    result = remove_generated_cues_from_playlist("playlist-1")

    assert db.deleted == [generated, generated_loop]
    assert all(cue is not manual for cue in db.deleted)
    assert all(cue is not external_named_like_generated for cue in db.deleted)
    assert managed.CueUpdated == "5"
    assert external.CueUpdated == "2"
    assert result.playlist_name == "Set"
    assert result.removed_cues == 2
    assert result.affected_tracks == 1
    assert result.backup_dir == backup
    assert db.committed is True
    assert db.closed is True


def test_beat_loop_size_uses_rekordbox_encoding():
    assert _beat_loop_size(CueHint("Loop", 1.0, "loop", 1, 5.0, 8)) == 524289


def test_rekordbox_color_waveform_lane_preserves_peak_colors_and_downsamples():
    tag = SimpleNamespace(
        type="PWV5",
        get=lambda: (
            [0.1, 0.8, 0.3, 0.5],
            [[0, 1, 2], [7, 6, 5], [3, 4, 5], [1, 2, 3]],
        ),
    )

    lane = _rekordbox_waveform_lane(tag, max_points=2)

    assert lane is not None
    assert lane.tag == "PWV5"
    assert lane.heights == [0.8, 0.5]
    assert lane.colors == [[255, 219, 182], [36, 73, 109]]


def test_downsample_waveform_uses_max_height_from_each_stable_bucket():
    heights, colors = _downsample_waveform(
        [0.1, 0.7, 0.2, 0.9, 0.4, 0.3],
        [[1, 1, 1], [2, 2, 2], [3, 3, 3], [4, 4, 4], [5, 5, 5], [6, 6, 6]],
        max_points=3,
    )

    assert heights == [0.7, 0.9, 0.4]
    assert colors == [[2, 2, 2], [4, 4, 4], [5, 5, 5]]


def test_rekordbox_cue_out_seconds_treats_negative_sentinel_as_unset():
    assert _rekordbox_cue_out_seconds(None) is None
    assert _rekordbox_cue_out_seconds(-1) is None
    assert _rekordbox_cue_out_seconds(194_842) == 194.842


def test_list_usb_devices_excludes_disk_images_and_reports_rekordbox_exports(monkeypatch, tmp_path):
    usb = tmp_path / "DJ USB"
    disk_image = tmp_path / "Installer"
    (usb / "PIONEER" / "rekordbox").mkdir(parents=True)
    disk_image.mkdir()

    def fake_run(command, **_kwargs):
        path = command[-1]
        if path == str(usb):
            payload = {
                "Internal": False,
                "BusProtocol": "USB",
                "RemovableMediaOrExternalDevice": True,
                "MountPoint": str(usb),
                "VolumeName": "DJ USB",
                "FilesystemType": "exfat",
                "TotalSize": 64_000,
                "FreeSpace": 40_000,
                "WritableVolume": True,
            }
        else:
            payload = {
                "Internal": False,
                "BusProtocol": "Disk Image",
                "RemovableMediaOrExternalDevice": True,
                "MountPoint": str(disk_image),
            }
        return SimpleNamespace(returncode=0, stdout=plistlib.dumps(payload))

    monkeypatch.setattr("app.rekordbox_sync.subprocess.run", fake_run)

    assert list_usb_devices(tmp_path) == [{
        "name": "DJ USB",
        "mount_path": str(usb),
        "filesystem": "exfat",
        "total_bytes": 64_000,
        "free_bytes": 40_000,
        "writable": True,
        "rekordbox_export": True,
    }]


def test_sync_cue_hints_adds_missing_hot_and_memory_cues():
    db = FakeDb()
    content = SimpleNamespace(ID="track-1", UUID="uuid-1", HotCueAutoLoad=None, CueUpdated=None, Commnt="")
    features = _features(
        [
            CueHint("Intro", 0.5, "hot", 0),
            CueHint("Phrase 16", 30.0, "hot", 1),
            CueHint("Outro", 120.0, "memory", None),
        ]
    )

    added, skipped, added_loops, skipped_loops = _sync_cue_hints(db, content, features)

    assert added == 3
    assert skipped == 0
    assert added_loops == 0
    assert skipped_loops == 0
    assert [cue.Kind for cue in db.added] == [1, 2, 0]
    assert [cue.InMsec for cue in db.added] == [500, 30000, 120000]
    assert all(cue.ContentID == "track-1" for cue in db.added)
    assert all(cue.ContentUUID == "uuid-1" for cue in db.added)
    assert content.HotCueAutoLoad == "on"
    assert content.CueUpdated == "3"


def test_sync_cue_hints_skips_existing_hot_slots_and_near_memory_cues():
    existing = [
        SimpleNamespace(Kind=1, InMsec=500),
        SimpleNamespace(Kind=0, InMsec=120200),
    ]
    db = FakeDb(existing)
    content = SimpleNamespace(ID="track-1", UUID="uuid-1", HotCueAutoLoad=None, CueUpdated="2", Commnt="")
    features = _features(
        [
            CueHint("Intro", 0.5, "hot", 0),
            CueHint("Phrase 16", 30.0, "hot", 1),
            CueHint("Outro", 120.0, "memory", None),
        ]
    )

    added, skipped, added_loops, skipped_loops = _sync_cue_hints(db, content, features)

    assert added == 1
    assert skipped == 2
    assert added_loops == 0
    assert skipped_loops == 0
    assert [cue.Kind for cue in db.added] == [2]
    assert content.CueUpdated == "3"


def test_sync_cue_hints_adds_loop_rows_with_rekordbox_loop_fields():
    db = FakeDb()
    content = SimpleNamespace(ID="track-1", UUID="uuid-1", HotCueAutoLoad=None, CueUpdated=None, Commnt="")
    features = _features(
        [
            CueHint("Intro", 0.5, "hot", 0),
            CueHint("Intro Loop", 0.5, "loop", 3, 4.5, 8),
            CueHint("Exit Loop", 180.0, "loop", 4, 184.0, 8),
        ]
    )

    added, skipped, added_loops, skipped_loops = _sync_cue_hints(db, content, features)

    assert added == 1
    assert skipped == 0
    assert added_loops == 2
    assert skipped_loops == 0
    assert [cue.Kind for cue in db.added] == [1, 5, 6]
    intro_loop = db.added[1]
    assert intro_loop.InMsec == 500
    assert intro_loop.OutMsec == 4500
    assert intro_loop.CueMicrosec == 0
    assert intro_loop.Color == 255
    assert intro_loop.ColorTableIndex == 0
    assert intro_loop.ActiveLoop == 0
    assert intro_loop.BeatLoopSize == 524289


def test_sync_cue_hints_skips_loop_when_hotcue_slot_exists():
    existing = [SimpleNamespace(Kind=5, InMsec=500, Comment="Manual Loop")]
    db = FakeDb(existing)
    content = SimpleNamespace(ID="track-1", UUID="uuid-1", HotCueAutoLoad=None, CueUpdated="1", Commnt="")
    features = _features([CueHint("Intro Loop", 0.5, "loop", 3, 4.5, 8)])

    added, skipped, added_loops, skipped_loops = _sync_cue_hints(db, content, features)

    assert added == 0
    assert skipped == 0
    assert added_loops == 0
    assert skipped_loops == 1
    assert db.added == []
    assert db.deleted == []


def test_sync_cue_hints_fill_mode_never_replaces_existing_slots():
    existing = [
        SimpleNamespace(Kind=1, InMsec=500, Comment="Intro"),
        SimpleNamespace(Kind=6, InMsec=180000, Comment="My Exit"),
    ]
    db = FakeDb(existing)
    content = SimpleNamespace(
        ID="track-1",
        UUID="uuid-1",
        HotCueAutoLoad=None,
        CueUpdated="2",
        Commnt="soundcloud-dl | https://soundcloud.com/test",
    )
    features = _features(
        [
            CueHint("Intro", 1.0, "hot", 0),
            CueHint("Phrase 16", 30.0, "hot", 1),
            CueHint("Exit Loop", 180.0, "loop", 4, 184.0, 8),
        ]
    )

    added, skipped, added_loops, skipped_loops = _sync_cue_hints(
        db,
        content,
        features,
        replace_generated=False,
    )

    assert db.deleted == []
    assert [(cue.Kind, cue.Comment) for cue in db.added] == [(2, "Phrase 16")]
    assert added == 1
    assert skipped == 1
    assert added_loops == 0
    assert skipped_loops == 1


def test_sync_cue_hints_replaces_old_soundcloud_dl_auto_cues():
    existing = [
        SimpleNamespace(Kind=1, InMsec=500, Comment="Intro"),
        SimpleNamespace(Kind=2, InMsec=30000, Comment="Phrase 16"),
        SimpleNamespace(Kind=3, InMsec=60000, Comment="Phrase 32"),
        SimpleNamespace(Kind=4, InMsec=180000, Comment="Exit Loop"),
        SimpleNamespace(Kind=0, InMsec=200000, Comment="Outro"),
        SimpleNamespace(Kind=7, InMsec=90000, Comment="Manual"),
    ]
    db = FakeDb(existing)
    content = SimpleNamespace(
        ID="track-1",
        UUID="uuid-1",
        HotCueAutoLoad=None,
        CueUpdated="6",
        Commnt="soundcloud-dl | https://soundcloud.com/test",
    )
    features = _features(
        [
            CueHint("Intro", 0.5, "hot", 0),
            CueHint("Phrase 16", 30.0, "hot", 1),
            CueHint("Phrase 32", 60.0, "hot", 2),
            CueHint("Intro Loop", 0.5, "loop", 3, 4.5, 8),
            CueHint("Exit Loop", 180.0, "loop", 4, 184.0, 8),
        ]
    )

    added, skipped, added_loops, skipped_loops = _sync_cue_hints(db, content, features)

    assert added == 3
    assert skipped == 0
    assert added_loops == 2
    assert skipped_loops == 0
    assert [cue.Comment for cue in db.deleted] == ["Intro", "Phrase 16", "Phrase 32", "Exit Loop", "Outro"]
    assert [cue.Kind for cue in db.added] == [1, 2, 3, 5, 6]
    assert content.CueUpdated == "11"


def test_rekordbox_xml_writes_loop_marks(tmp_path):
    out = tmp_path / "rekordbox.xml"
    features = _features([CueHint("Intro Loop", 0.5, "loop", 3, 4.5, 8)])

    write_rekordbox_xml([features], out)

    root = ElementTree.parse(out).getroot()
    mark = root.find(".//POSITION_MARK")
    assert mark is not None
    assert mark.attrib["Name"] == "Intro Loop"
    assert mark.attrib["Type"] == "4"
    assert mark.attrib["Start"] == "0.5"
    assert mark.attrib["End"] == "4.5"
    assert mark.attrib["Num"] == "3"


def test_reserved_slots_only_written_on_managed_tracks():
    from app.rekordbox_sync import RESERVED_EXTERNAL_SLOTS

    db = FakeDb()
    content = SimpleNamespace(ID="track-1", UUID="uuid-1", HotCueAutoLoad=None, CueUpdated=None, Commnt="user notes")
    features = _features(
        [
            CueHint("Intro", 0.5, "hot", 0),
            CueHint("Breakdown", 80.0, "hot", 5),
            CueHint("Last Drop", 120.0, "hot", 6),
        ]
    )

    added, _, _, _ = _sync_cue_hints(db, content, features)

    assert added == 1
    written_slots = {cue.Kind for cue in db.added}
    # Only Intro (kind 1, slot 0) is written. Reserved slots 5/6 stay untouched.
    assert written_slots == {1}
    assert 5 in RESERVED_EXTERNAL_SLOTS and 6 in RESERVED_EXTERNAL_SLOTS


def test_reserved_slots_written_on_soundcloud_dl_managed_tracks():
    db = FakeDb()
    content = SimpleNamespace(
        ID="track-1",
        UUID="uuid-1",
        HotCueAutoLoad=None,
        CueUpdated=None,
        Commnt="soundcloud-dl | https://soundcloud.com/x/y",
    )
    features = _features(
        [
            CueHint("Intro", 0.5, "hot", 0),
            CueHint("Breakdown", 80.0, "hot", 5),
            CueHint("Last Drop", 120.0, "hot", 6),
        ]
    )

    added, _, _, _ = _sync_cue_hints(db, content, features)

    assert added == 3
    written_kinds = [cue.Kind for cue in db.added]
    assert written_kinds == [1, 7, 8]  # slot 5 -> kind 7, slot 6 -> kind 8


def test_auto_cue_names_includes_every_emitted_cue_name():
    from app.audio_features import _cue_hints, Section
    from app.rekordbox_sync import AUTO_CUE_NAMES

    heuristic = {cue.name for cue in _cue_hints(duration=240.0, bpm=120.0, first_downbeat=0.5)}
    structural_sections = [
        Section(0.0, 32.0, "intro", 0.9),
        Section(32.0, 64.0, "build", 0.9),
        Section(64.0, 128.0, "drop", 0.9),
        Section(128.0, 160.0, "breakdown", 0.9),
        Section(160.0, 192.0, "last_drop", 0.9),
        Section(192.0, 240.0, "outro", 0.9),
    ]
    structural = {
        cue.name
        for cue in _cue_hints(
            duration=240.0,
            bpm=120.0,
            first_downbeat=0.5,
            sections=structural_sections,
            segmentation_mode="structural",
        )
    }
    # Loops only emit when a profile is present; that's fine — name allowlist must still cover them.
    emitted = heuristic | structural | {"Intro Loop", "Exit Loop"}
    missing = emitted - AUTO_CUE_NAMES
    assert not missing, f"missing names from AUTO_CUE_NAMES: {missing}"


def test_resync_is_idempotent_no_changes_on_second_push():
    content = SimpleNamespace(
        ID="track-1",
        UUID="uuid-1",
        HotCueAutoLoad=None,
        CueUpdated=None,
        Commnt="soundcloud-dl | https://soundcloud.com/x/y",
    )
    features = _features(
        [
            CueHint("Intro", 0.5, "hot", 0),
            CueHint("Phrase 16", 30.0, "hot", 1),
            CueHint("Phrase 32", 60.0, "hot", 2),
            CueHint("Intro Loop", 0.5, "loop", 3, 4.5, 8),
            CueHint("Exit Loop", 180.0, "loop", 4, 184.0, 8),
            CueHint("Outro", 200.0, "memory", None),
        ]
    )

    # First push from an empty DB.
    db = FakeDb()
    first_added, _, first_added_loops, _ = _sync_cue_hints(db, content, features)
    assert first_added + first_added_loops == 6

    # Build "existing" state that mirrors what the first push wrote.
    existing = [
        SimpleNamespace(Kind=cue.Kind, InMsec=cue.InMsec, Comment=cue.Comment, OutMsec=cue.OutMsec)
        for cue in db.added
    ]
    db2 = FakeDb(existing)
    added, _skipped, added_loops, _skipped_loops = _sync_cue_hints(db2, content, features)

    # Re-push removes the previously-generated cues (managed track) then re-writes them, so
    # net adds equal the cue count and there are no duplicate (Kind, InMsec) pairs in the DB.
    final_state = [
        (cue.Kind, cue.InMsec)
        for cue in (existing + db2.added)
        if cue not in db2.deleted
    ]
    assert len(final_state) == len(set(final_state)), f"duplicate cues after re-push: {final_state}"
    assert added + added_loops == 6


def test_comment_includes_structure_summary_and_vocal_class():
    from app.rekordbox_sync import _comment

    features = TrackFeatures(
        path="/tmp/test.mp3",
        title="Track",
        artist="Artist",
        duration_sec=240.0,
        sample_rate=44100,
        bpm=124.0,
        musical_key="Am",
        camelot_key="8A",
        key_confidence=0.9,
        loudness_dbfs=-9.0,
        peak_dbfs=-0.1,
        energy=8,
        first_downbeat_sec=0.5,
        cue_hints=[
            CueHint("Intro", 0.5, "hot", 0),
            CueHint("Drop", 134.5, "hot", 2),
        ],
        source_url="https://soundcloud.com/x/y",
        vocal_class="dub",
        tempo_stable=True,
    )

    comment = _comment(features)
    assert "drop @ 2:14" in comment
    assert "dub" in comment
    assert "8A" in comment


def _features(cues):
    return TrackFeatures(
        path="/tmp/test.mp3",
        title="Test",
        artist="Artist",
        duration_sec=180.0,
        sample_rate=44100,
        bpm=128.0,
        musical_key="Dm",
        camelot_key="7A",
        key_confidence=0.8,
        loudness_dbfs=-10.0,
        peak_dbfs=-0.1,
        energy=8,
        first_downbeat_sec=0.5,
        cue_hints=cues,
    )
