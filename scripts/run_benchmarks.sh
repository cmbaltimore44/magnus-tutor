#!/bin/sh
# Full benchmark: latency/memory, tutor role (no thinking), solver role (thinking), concurrency.
set -x
T=.venv/bin/tutor
ALL="qwen3.5:9b gemma4:12b qwen3:14b deepseek-r1:14b qwen3.5:4b qwen2.5-coder:7b"
$T bench latency --models $ALL
$T bench run --roles tutor --models $ALL
$T bench run --roles solver --models qwen3.5:9b qwen3:14b gemma4:12b deepseek-r1:14b qwen3.5:4b
$T bench concurrency --models qwen3.5:9b
$T bench report
