from __future__ import annotations

from types import SimpleNamespace
from xml.etree import ElementTree

from app.audio_features import CueHint, TrackFeatures
from app.rekordbox_sync import _beat_loop_size, _cue_kind, _sync_cue_hints, write_rekordbox_xml


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeDb:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.added = []
        self.deleted = []
        self.next_id = 1000

    def get_cue(self, **kwargs):
        assert kwargs["ContentID"] == "track-1"
        return FakeQuery(self.existing)

    def generate_unused_id(self, table, is_28_bit=True):
        assert not is_28_bit
        self.next_id += 1
        return self.next_id

    def add(self, instance):
        self.added.append(instance)

    def delete(self, instance):
        self.deleted.append(instance)


def test_cue_kind_maps_hotcue_slots_to_rekordbox_kinds():
    assert _cue_kind(CueHint("A", 1.0, "hot", 0)) == 1
    assert _cue_kind(CueHint("C", 1.0, "hot", 2)) == 3
    assert _cue_kind(CueHint("D", 1.0, "hot", 3)) == 5
    assert _cue_kind(CueHint("E", 1.0, "hot", 4)) == 6
    assert _cue_kind(CueHint("Memory", 1.0, "memory", None)) == 0
    assert _cue_kind(CueHint("Bad", 1.0, "hot", 8)) is None
    assert _cue_kind(CueHint("Intro Loop", 1.0, "loop", 3, 5.0, 8)) == 5


def test_beat_loop_size_uses_rekordbox_encoding():
    assert _beat_loop_size(CueHint("Loop", 1.0, "loop", 1, 5.0, 8)) == 524289


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


def test_sync_cue_hints_replaces_old_soundcloud_dl_auto_cues():
    existing = [
        SimpleNamespace(Kind=1, InMsec=500, Comment="Intro"),
        SimpleNamespace(Kind=2, InMsec=30000, Comment="Phrase 16"),
        SimpleNamespace(Kind=3, InMsec=60000, Comment="Phrase 32"),
        SimpleNamespace(Kind=4, InMsec=180000, Comment="Exit Loop"),
        SimpleNamespace(Kind=7, InMsec=90000, Comment="Manual"),
    ]
    db = FakeDb(existing)
    content = SimpleNamespace(
        ID="track-1",
        UUID="uuid-1",
        HotCueAutoLoad=None,
        CueUpdated="5",
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
    assert [cue.Comment for cue in db.deleted] == ["Intro", "Phrase 16", "Phrase 32", "Exit Loop"]
    assert [cue.Kind for cue in db.added] == [1, 2, 3, 5, 6]
    assert content.CueUpdated == "10"


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
