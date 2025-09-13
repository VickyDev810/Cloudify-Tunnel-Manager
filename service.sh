#!/bin/bash
# Start cron in the foreground
cron -f &
# Start your app
cloudify serve
