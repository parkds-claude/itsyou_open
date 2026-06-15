#!/usr/bin/env bash
set -e
echo "== itsyou_open 설치 =="
pkg update -y && pkg install -y python git
git clone https://github.com/parkds-claude/itsyou_open.git 2>/dev/null || (cd itsyou_open && git pull)
cd itsyou_open
pip install -r requirements.txt
echo "== 설치 완료. 실행: python app.py 후 브라우저에서 http://localhost:5080 =="
python app.py
