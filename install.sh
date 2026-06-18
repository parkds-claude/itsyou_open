#!/usr/bin/env bash
set -e
echo "== itsyou_open 설치 =="

# 1) 기본 패키지
pkg update -y && pkg install -y python git

# 2) Pillow 는 Termux 에서 pip 로 소스 컴파일하면 자주 실패한다(libjpeg/zlib 헤더).
#    미리 빌드된 패키지를 먼저 깔아 두면 아래 pip 단계가 이를 그대로 사용한다.
pkg install -y python-pillow

# 3) 소스 받기(이미 있으면 최신화)
git clone https://github.com/parkds-claude/itsyou_open.git 2>/dev/null || (cd itsyou_open && git pull)
cd itsyou_open

# 4) 나머지 의존성(pillow 는 위에서 충족되어 재컴파일하지 않음)
pip install -r requirements.txt

echo "== 설치 완료. 실행: python app.py 후 브라우저에서 http://localhost:5080 =="
python app.py
