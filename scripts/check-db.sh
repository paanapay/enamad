#!/bin/bash
cd /srv/enamad
PW=$(grep '^MYSQL_PASSWORD=' .env | head -1 | cut -d= -f2- | tr -d '\r')
docker compose exec -T mysql mariadb -uroot -p"$PW" -N -e "SELECT COUNT(*) FROM enamad_domains; SELECT COUNT(*) FROM bot_users;" enamad
