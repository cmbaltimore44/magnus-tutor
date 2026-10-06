"""Languages the code sandbox can run or check, defined in languages.yaml.

Each entry:
  extensions: [.py]          files that belong to it
  requires: python3          command that must exist (or an absolute path)
  run: "{python} {file}"     how to run a file (mode: run), or
  check: "hadolint {file}"   how to check a config file (mode: check)
  mode: run | check
  highlight: python          editor syntax mode
  allow_fork: false          programs may not start subprocesses (set true only if a toolchain needs it)
  hello: |                   tiny program used by `tutor languages check`
  expect: "hello"            output the hello program should print

Placeholders: {file} the main file, {dir} the temp working dir, {python} the
sandbox's own Python (a separate venv, not the tutor's), {stem} file name
without extension.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from .config import Paths, paths

DEFAULT_LANGUAGES_YAML = """\
# Languages the tutor's code sandbox knows. Add one: install its toolchain,
# add an entry here (copy one below), list it in a course's `languages:`, then
# run `tutor languages check`. Every language runs in the same sandbox with
# the same limits (CPU time, memory, processes, wall clock, no network).

python:
  extensions: [.py]
  requires: "{python}"
  run: "{python} {file}"
  highlight: python
  hello: 'print("hello")'
  expect: hello

sml:
  extensions: [.sml, .sig]
  requires: poly
  run: "poly --script {file}"
  highlight: sml
  hello: 'val () = print "hello\\n";'
  expect: hello

java:
  extensions: [.java]
  requires: java
  # single-file source launch; the first top-level class must have main()
  run: "java -Xmx256m -XX:+UseSerialGC -XX:TieredStopAtLevel=1 {file}"
  highlight: java
  hello: |
    public class Main { public static void main(String[] a) { System.out.println("hello"); } }
  filename: Main.java
  expect: hello

ruby:
  extensions: [.rb]
  # Homebrew Ruby (keg-only, not on your PATH)
  requires: /opt/homebrew/opt/ruby/bin/ruby
  run: "/opt/homebrew/opt/ruby/bin/ruby {file}"
  highlight: ruby
  hello: 'puts "hello"'
  expect: hello

dockerfile:
  extensions: [Dockerfile, .dockerfile]
  mode: check
  requires: hadolint
  check: "hadolint --no-color {file}"
  highlight: dockerfile
  hello: |
    FROM python:3.12-slim
    CMD ["python", "-c", "print('hi')"]
  filename: Dockerfile

kubernetes:
  extensions: [.yaml, .yml]
  mode: check
  requires: kubeconform
  # kubeconform downloads JSON schemas once (cached in {cache}); only check-mode
  # tools may use allow_network, and code is never run with it.
  check: "kubeconform -strict -summary -cache {cache} {file}"
  allow_network: true
  highlight: yaml
  hello: |
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: hello
    data:
      greeting: hello
  filename: hello.yaml
"""


def load(p: Paths | None = None) -> dict[str, dict]:
    p = p or paths()
    try:
        data = yaml.safe_load(p.languages.read_text()) or {}
    except FileNotFoundError:
        data = yaml.safe_load(DEFAULT_LANGUAGES_YAML)
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def for_filename(name: str, langs: dict[str, dict]) -> str | None:
    for key, spec in langs.items():
        for ext in spec.get("extensions", []):
            if name == ext or name.endswith(ext):
                return key
    return None


def requirement_ok(spec: dict, python: str) -> bool:
    req = str(spec.get("requires", "")).replace("{python}", python)
    if not req:
        return True
    return Path(req).exists() if req.startswith("/") else shutil.which(req) is not None
