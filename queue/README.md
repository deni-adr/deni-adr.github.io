Put one JSON file per scheduled post here, e.g. queue/2026-10-28-0900.json:
{"at": "2026-10-28T02:00:00Z", "video": "videos/clip.mp4", "caption": "Produk keren #affiliate", "privacy": "SELF_ONLY"}
"at" is UTC. The queue workflow runs every 30 minutes, posts due items and moves them to queue/done/.
