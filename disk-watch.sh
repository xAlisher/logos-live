#!/bin/sh
while true; do
  df -h /data | awk 'NR==2{printf "/data  free: %s  used: %s (%s)\n", $4, $3, $5}'
  sleep 60
done
