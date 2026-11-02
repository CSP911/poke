# 제품팀 8시간 제안 (2026-10-11, 착수 전)

| # | 작업 | 시간 | 파일 | 합격 기준 |
|---|---|---|---|---|
| 1 | 문장 기록 + 정규화·파라미터 캐시 | 1.5h | 새 sentence/record.py, say.py make_op, (rt.h param() — 런타임 소유자 합의) | 기록 = 문장 변형들·의도 키·파라미터 스키마·op·manifest·승인·결과 흔적·콜로폰(모델·날짜·카탈로그 해시). "LED 3번 켜줘" 뒤 "LED 4번 켜줘"·"LED 4번 켜 줘"는 op 새로 쓰기 0회. demo 7/7 유지 |
| 2 | manifest + 쓰기 확인 + NEED 되묻기 | 1.5h | say.py OP_SYSTEM/op_prompt/run, record.py | "불 좀 켜줘" → 부팅 없이 되물음. 쓰기 문장 → 한국어 확인 한 줄(--yes로 생략, 승인은 기록에 저장). rtc_set 문장 거부. manifest 밖 조각 링크 시 거부(검사기 API) |
| 3 | 사람 말 메시지·한국어 설명 | 1h | 새 sentence/messages_ko.py, say.py main/run | 실패 6종(claude 없음, qemu 없음, 검사 거부, 시간 초과, NEED, 할 수 없는 요청)이 traceback 없이 한국어 1–2줄(--debug에만 traceback). 결과 요약 한국어 + 단위·해상도·KST |
| 4 | 허브 예약 부팅(20s 초과) | 1h | 새 sentence/schedule.py, say.py | "30초 동안 10초마다 온도" → 4회 부팅, 합친 결과, 부팅 사이 노드 비움 표시. 무기한 요청은 상한 안내 |
| 5 | 데모 화면(트윈) | 2.5h | 새 sentence/ui/ (server.py, index.html; 표준 라이브러리 HTTP+SSE) | 명령 하나로 브라우저: 문장 → op → manifest/확인 → 검사 → 부팅 → 결과 → "노드 비움" 단계 표시, LED 16개·온도 슬라이더로 실험대 시각화. 새 문장·캐시 문장·할 수 없는 요청·되물음 4장면 |
| 6 | 회귀·README·아침 안내 | 0.5h | demo.py, README.md, teams/product/morning.md | 시험팀 회귀 통과, 소유자용 "써볼 문장 10개" |

## 의존
- 검사기팀: v1 런타임을 edge/sentence에 확정, 조각 read/write 분리, "링크된 조각 함수 ⊆ manifest" 확인 함수, param 영역 허용.
- 시험팀: 각 단위 뒤 회귀, 모호·변형 문장 코퍼스.
- 실기기팀: 없음(0 채우기는 런타임 쪽).

## 소유 파일
sentence/record.py, schedule.py, messages_ko.py, ui/, say.py의 run()·CLI·OP_SYSTEM·make_op (check()·boot()는 검사기·시험팀).

## 소유자가 깨어나 써볼 것
`cd sentence && ../sim/body/.venv/bin/python ui/server.py` → 브라우저에서 한국어로 말하기, 단계와 노드 비움을 보고 실험대 LED가 바뀌는 것 확인. CLI도 같은 한국어. 아침 안내 한 장(되는 것·안 되는 것·숫자).
