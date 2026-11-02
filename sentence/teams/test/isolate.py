"""소유자 기록 보호(회의 결정 7): 시험 스크립트가 맨 먼저 import 한다. POKE_RECORDS가 비어 있으면 소유자 기록
(sentence/build/records)을 읽기만 해서 임시 디렉터리로 복사하고 그쪽을 쓰게 한다. 하위 프로세스도 환경으로 물려받는다.
끝나면 임시 디렉터리를 지운다."""
import os, shutil, tempfile, atexit
OWNER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "build", "records")
if not os.environ.get("POKE_RECORDS"):
    _d = tempfile.mkdtemp(prefix="poke-test-records-")
    if os.path.isdir(OWNER):
        for f in os.listdir(OWNER):
            if f.endswith(".json"):
                shutil.copy2(os.path.join(OWNER, f), os.path.join(_d, f))
    os.environ["POKE_RECORDS"] = _d
    atexit.register(shutil.rmtree, _d, True)
