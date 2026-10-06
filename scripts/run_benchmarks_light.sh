#!/bin/sh
# Reduced, paced benchmark (cool-downs between problems so the laptop stays cool).
set -x
T=.venv/bin/tutor
CODE="sml-sum sml-reverse sml-fib sml-tree-depth sml-count-if sml-lookup py-wordcount py-duration java-gcd docker-flask k8s-deployment k8s-service"
SOLVE="em-point-charge em-line-charge em-capacitor em-solid-sphere em-wire-field em-faraday qm-well-ground qm-electron-well qm-normalize qm-de-broglie qm-hydrogen math-integral"
$T bench run --roles tutor --models qwen3.5:4b --cooldown 5
$T bench run --roles tutor --models qwen2.5-coder:7b --ids $CODE --cooldown 5
$T bench run --roles solver --models qwen3.5:9b qwen3.5:4b --ids $SOLVE --cooldown 20
$T bench concurrency --models qwen3.5:9b
$T bench regrade
