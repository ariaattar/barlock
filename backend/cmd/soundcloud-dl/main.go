package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

func main() {
	backendDir, err := findBackendDir()
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}

	if shouldLaunchTUI(os.Args[1:]) {
		runTUI(backendDir)
		return
	}

	runPythonCLI(backendDir, os.Args[1:])
}

func runPythonCLI(backendDir string, cliArgs []string) {
	uv, err := lookPath("uv")
	if err != nil {
		fmt.Fprintln(os.Stderr, "error: uv is required on PATH")
		os.Exit(1)
	}

	args := append([]string{"run", "python", "-m", "app.soundcloud_cli"}, cliArgs...)
	cmd := exec.Command(uv, args...)
	cmd.Dir = backendDir
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Env = append(os.Environ(), "PATH="+launcherPath())

	runCommand(cmd)
}

func runTUI(backendDir string) {
	bun, err := lookPath("bun")
	if err != nil {
		fmt.Fprintln(os.Stderr, "error: bun is required for interactive OpenTUI mode")
		os.Exit(1)
	}

	cmd := exec.Command(bun, "src/main.ts")
	cmd.Dir = filepath.Join(backendDir, "tui")
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Env = append(os.Environ(), "PATH="+launcherPath())

	runCommand(cmd)
}

func runCommand(cmd *exec.Cmd) {
	if err := cmd.Run(); err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			os.Exit(exitErr.ExitCode())
		}
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func shouldLaunchTUI(args []string) bool {
	return len(args) == 0 || (len(args) == 1 && (args[0] == "--interactive" || args[0] == "-i"))
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

	return "", errors.New("could not find backend app; set MIXER_BACKEND_DIR")
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
	if _, err := os.Stat(filepath.Join(abs, "tui", "src", "main.ts")); err != nil {
		return "", err
	}
	return abs, nil
}

func lookPath(name string) (string, error) {
	if path, err := exec.LookPath(name); err == nil {
		return path, nil
	}
	for _, dir := range strings.Split(launcherPath(), string(os.PathListSeparator)) {
		if dir == "" {
			continue
		}
		candidate := filepath.Join(dir, name)
		if stat, err := os.Stat(candidate); err == nil && !stat.IsDir() && stat.Mode()&0111 != 0 {
			return candidate, nil
		}
	}
	return "", fmt.Errorf("%s not found", name)
}

func launcherPath() string {
	path := os.Getenv("PATH")
	for _, extra := range []string{"/opt/homebrew/bin", "/usr/local/bin", filepath.Join(os.Getenv("HOME"), ".bun", "bin")} {
		if _, err := os.Stat(extra); err == nil {
			path = extra + string(os.PathListSeparator) + path
		}
	}
	return path
}
