package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

func main() {
	backendDir, err := findBackendDir()
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}

	uv, err := exec.LookPath("uv")
	if err != nil {
		fmt.Fprintln(os.Stderr, "error: uv is required on PATH")
		os.Exit(1)
	}

	args := append([]string{"run", "python", "-m", "app.soundcloud_cli"}, os.Args[1:]...)
	cmd := exec.Command(uv, args...)
	cmd.Dir = backendDir
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Env = append(os.Environ(), "PATH="+launcherPath())

	if err := cmd.Run(); err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			os.Exit(exitErr.ExitCode())
		}
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func findBackendDir() (string, error) {
	if env := os.Getenv("MIXER_BACKEND_DIR"); env != "" {
		return validateBackendDir(env)
	}

	if exe, err := os.Executable(); err == nil {
		for _, executable := range executablePaths(exe) {
			exeDir := filepath.Dir(executable)
			candidates := []string{
				filepath.Dir(exeDir),               // backend/bin/soundcloud-dl
				exeDir,                             // backend/soundcloud-dl
				filepath.Dir(filepath.Dir(exeDir)), // nested install fallback
			}
			for _, candidate := range candidates {
				if dir, err := validateBackendDir(candidate); err == nil {
					return dir, nil
				}
			}
		}
	}

	if cwd, err := os.Getwd(); err == nil {
		for dir := cwd; ; dir = filepath.Dir(dir) {
			if validated, err := validateBackendDir(dir); err == nil {
				return validated, nil
			}
			parent := filepath.Dir(dir)
			if parent == dir {
				break
			}
		}
	}

	return "", errors.New("could not find backend/app/soundcloud_cli.py; set MIXER_BACKEND_DIR")
}

func executablePaths(exe string) []string {
	paths := []string{exe}
	if resolved, err := filepath.EvalSymlinks(exe); err == nil && resolved != exe {
		paths = append(paths, resolved)
	}
	return paths
}

func validateBackendDir(dir string) (string, error) {
	abs, err := filepath.Abs(dir)
	if err != nil {
		return "", err
	}
	stat, err := os.Stat(filepath.Join(abs, "app", "soundcloud_cli.py"))
	if err != nil {
		return "", err
	}
	if stat.IsDir() {
		return "", errors.New("soundcloud_cli.py is a directory")
	}
	return abs, nil
}

func launcherPath() string {
	path := os.Getenv("PATH")
	for _, extra := range []string{"/opt/homebrew/bin", "/usr/local/bin"} {
		if _, err := os.Stat(extra); err == nil {
			path = extra + string(os.PathListSeparator) + path
		}
	}
	return path
}
