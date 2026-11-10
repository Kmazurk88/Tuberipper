docker rm -f tr-test >/dev/null 2>&1
docker run -d --name tr-test --network arr --user 99:100 \
 -e NAVIDROME_URL=http://navidrome:4533 -e NAVIDROME_USER=tuberipper -e NAVIDROME_PASSWORD="$(docker exec tuberipper printenv NAVIDROME_PASSWORD)" \
 -e TUBERIPPER_CACHE_DIR=/cache -e TUBERIPPER_OUTPUT_DIR=/music -e TUBERIPPER_PODCASTS_DIR=/podcasts -e TZ=UTC \
 -v /mnt/cache/appdata/tuberipper/build/tuberipper:/app/tuberipper:ro -v /mnt/user/Media/music:/music:ro -v /mnt/user/Media/podcasts:/podcasts:ro -v /tmp/trcache:/cache \
 tuberipper:local >/dev/null
sleep 6
