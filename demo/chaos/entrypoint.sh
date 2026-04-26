#!/bin/bash
mkdir -p /run/sshd /var/log/nginx /var/run/nginx
/usr/sbin/sshd
exec nginx -g 'daemon off;'
