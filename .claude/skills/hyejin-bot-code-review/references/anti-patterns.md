# Anti-Patterns Catalog

리뷰에서 인용 가능한 룰 카탈로그. **여기 있는 ID/이름만** 인용한다 — 창작 금지. 적합 ID가 없으면 평문으로 룰을 서술하고, 반복 출현 시 카탈로그 추가 제안.

순서:
1. [Clean Code — Naming `[N*]`](#clean-code--naming-n)
2. [Clean Code — Functions `[F*]`](#clean-code--functions-f)
3. [Clean Code — General `[G*]`](#clean-code--general-g)
4. [Clean Code — Comments `[C*]`](#clean-code--comments-c)
5. [Pipeline Reliability `[P*]`](#pipeline-reliability-p)
6. [Test Determinism `[T*]`](#test-determinism-t)
7. [Secret & Runner Safety `[S*]`](#secret--runner-safety-s)
8. [Observability `[O*]`](#observability-o)
9. [Infrastructure-as-Code `[I*]`](#infrastructure-as-code-i)
10. [NPU Lab `[L*]`](#npu-lab-l)
11. [Clean Architecture `[A*]`](#clean-architecture-a)
12. [Spec / Process Drift `[D*]`](#spec--process-drift-d)

> **카탈로그 출처**: base는 upstream(`upstream-code-review-reference`) — 대연님의 누적된 카탈로그를 그대로 이어받음. hyejin delta는 `[G50]`, `[T20]`–`[T23]`, `[D20]`–`[D24]`, `[A1]`–`[A5]` 총 14개 룰. ssw-bundle / Robot Framework / release backport / layered service architecture 같은 한혜진의 자주 보는 영역을 보강.

---

## Clean Code — Naming `[N*]`

| ID | Name | Default severity | When to flag |
|---|---|---|---|
| `[N1]` | Descriptive names | MINOR (MAJOR if API) | `data`, `info`, `tmp`, `obj`, `mgr`, `helper`. 의미 없는 일반어. |
| `[N2]` | Names at appropriate abstraction level | MINOR | 저수준 모듈에 high-level 도메인 이름, 또는 그 반대. |
| `[N3]` | Standard nomenclature | MINOR | 팀 컨벤션과 다른 단어 (`fetch` vs `get`, `delete` vs `remove`). |
| `[N4]` | Unambiguous names | MAJOR | `process()` / `handle()` / `check()` — 동사가 모호. |
| `[N5]` | Long names for long scopes | MINOR | 모듈/전역 심볼이 너무 짧음 (`x`, `s`, `n`). |
| `[N6]` | Avoid encodings | MINOR | 헝가리언, `_ptr`, `i_count` 같은 타입 인코딩. |
| `[N7]` | Names describe side effects | MAJOR | `get_X()`인데 캐시 갱신/네트워크 호출. **자주 잡힌다.** |

**Pet peeve**: `get_*`이 부수효과를 가짐 → `[N7]`로 즉시 MAJOR.

---

## Clean Code — Functions `[F*]`

| ID | Name | Default severity | When to flag |
|---|---|---|---|
| `[F1]` | Too many arguments | MAJOR (≥4) | 인자 4개 이상이면 dataclass / config 객체로 묶기. |
| `[F2]` | Output arguments | MAJOR | 인자를 mutate해서 반환 대신 사용. 불변성 위반. |
| `[F3]` | Flag arguments | MINOR | `do_thing(force=True)` 같은 boolean flag. 함수 분리 권장. |
| `[F4]` | Dead function | MINOR | 어디서도 호출 안 됨. 삭제. |

**Pet peeve**: output argument(`[F2]`) — 불변성 원칙(global rule) 위반이라 자동 MAJOR.

---

## Clean Code — General `[G*]`

이 표는 사용자가 실제로 인용하는 ID만 추려놓은 것. 번호가 비어 있는 자리(`[G1]`, `[G4]`, `[G6]`–`[G8]`, `[G10]`–`[G24]` …)는 *비어 있는 게 아니라 인용 대상이 아닌 것* — 임의로 채워 넣어 새 룰을 만들지 말 것 (line 3의 "창작 금지"가 그대로 적용된다). 새 ID가 필요하면 카탈로그 확장 제안.

| ID | Name | Default severity | When to flag |
|---|---|---|---|
| `[G2]` | Obvious behavior unimplemented | MAJOR | 함수 이름이 약속한 동작을 구현하지 않음. |
| `[G3]` | Incorrect behavior at boundaries | CRITICAL | off-by-one, empty list, None, 빈 string 처리 누락. |
| `[G5]` | Duplication | MAJOR | 같은 로직 3번 이상 복붙. 1–2번은 OK (premature abstraction 회피). |
| `[G9]` | Dead code | MINOR | 도달 불가 코드 / 사용 안 하는 import. (Silent try/except는 `[G35]`로 분리.) |
| `[G25]` | Replace magic numbers | MINOR | `sleep(5)`, `timeout=30`, `retry=3` — 이름 없는 상수. **CRITICAL only when** the magic number is `timeout-minutes` / `retry` count / rate-limit on a regression-blocking job (회귀가 그 값에 직접 묶여 있음). |
| `[G28]` | Encapsulate conditionals | MINOR | 복잡한 boolean을 명명된 함수로. |
| `[G30]` | Functions do one thing | MAJOR | 한 함수가 fetch + parse + write. 분리. |
| `[G34]` | Descend one level of abstraction | MAJOR | 한 함수에 여러 추상화 레벨 혼재 (`http.get()` 옆에 `int(x[0]+1)`). |
| `[G35]` | Swallowed exception | CRITICAL | bare `except:` 또는 `except Exception: pass` — log/raise/typed-error 변환 없음. observability gap + silent failure. narrow exception type + structured log + propagate(or typed `Retry`/`DeadLetter`) 필수. |
| `[G50]` | Guard-repetition without root extraction | MAJOR | "fall-through 방지" 가드를 같은 패턴으로 N회(보통 ≥3) 반복 추가. **가드 반복이 곧 구조적 안티패턴 신호** — guard가 필요한 구조 자체가 원인이므로 도메인 예외 / 어댑터 분리 / 책임 추출까지 같은 PR에서 정리해야 한다. PR 리뷰 코멘트 응답으로 surface fix만 들어온 경우 특히 주의. |
| `[G36]` | Naive/aware datetime mix | MAJOR | tz 정보가 없을 수 있는 외부 timestamp(`email.utils.parsedate_to_datetime`, RSS/HTTP 헤더, 사용자 입력 등)를 tz-aware 값과 직접 비교/연산. naive↔aware 비교는 `TypeError: can't compare offset-naive and offset-aware`로 그 경로 전체를 깨뜨린다. 파싱 직후 `tzinfo` 보강(`replace(tzinfo=UTC)`) 또는 둘 다 normalize. |
| `[G37]` | Fail-open contradicts stated contract | MAJOR | 누락/파싱실패 데이터를 통과시키는데 함수의 계약(docstring/이름/`<=N`/"within window")은 "유효/범위 내만"이라고 주장. 예: pubDate 없는 항목을 recency 필터에서 keep, 검증 실패를 빈 결과 대신 통과. fail-open이 의도면 계약을 그에 맞게 고치고, 아니면 drop/raise. (계약 쪽이 거짓이면 `[D25]`와 함께.) |
| `[G38]` | Idempotency state advanced only on happy path | MAJOR | "한 번만 실행" 가드의 state(`last_fired_date`, `processed=1`, seen-set …)를 side-effect가 **새로 일어났을 때만** 갱신하고, 이미-처리됨/dedup된 경우엔 갱신 안 함. 결과: dedup으로 emit/insert가 no-op이 되면 state가 영영 안 움직여 **매 tick/재시도마다 같은 작업을 반복**(deploy 중첩 시 하루 종일 헛 INSERT). "side-effect를 내가 했든, 이미 되어 있든" 둘 다 처리 완료로 보고 state를 전진시켜야 한다. UNIQUE 충돌로 인한 dedup=False도 "이미 처리됨"의 신호다. |
| `[G51]` | Ticket reference in code comment | MINOR | 소스 파일·YAML·셸 스크립트 주석에 `DOLIN-NNNN` / `SSWCI-NNNNN` / `JIRA-XXX` 같은 작업 컨텍스트 티켓 ID가 박혀있음. **이 메타데이터는 PR description / commit message에 있어야 하며 코드베이스가 진화하면 stale 정보로 썩는다** (티켓이 close되거나 의미가 분기되어도 주석은 그대로). 도메인 배경 설명은 유지하되 티켓 ID만 떼고 평문으로 (`# square CLI for the rsys→square reservation migration (DOLIN-2885)` → `# square CLI for the rsys→square reservation migration`). 예외: 워크어라운드의 upstream bug tracker (`# upstream python/cpython#NNNN`), ADR 본문 인용 (`# see ADR-0005 §D6`)은 영구성이 있어 허용. |

**Pet peeve**:
- `try: ... except Exception: pass` → `[G35]` 즉시 CRITICAL. daily regression이 silent 실패로 며칠 가는 1순위 원인.
- magic number가 regression-blocking job의 `timeout-minutes` / `retry` / rate-limit이면 → `[G25]` CRITICAL (그 숫자가 flake source).
- `[G50]` guard 반복 → "표면 fix"가 자기복제하는 신호. `BuiltIn().fail()` 뒤에 `raise AssertionError("unreachable")` 가드를 5번 반복한 PR은 도메인 예외(`*Error` 클래스) + 순수 파서 분리가 본질 해결. 사용자가 명시적으로 "표면 fix만"이라고 한 경우는 그 범위 존중.
- `[G36]` naive datetime → 로컬 테스트는 tz-aware 입력만 써서 통과하고, 실제 피드/헤더가 tz를 빠뜨리는 순간 프로덕션에서만 터진다. 외부 timestamp는 "tz 없을 수 있다" 전제로.
- `[G38]` "emit 성공했을 때만 mark_fired" → dedup hit(다른 인스턴스가 이미 함)에서 state가 멈춰 retry storm. `if emitted: mark()` 가 보이면 "이미 처리된 경우는?"를 물어라.

---

## Clean Code — Comments `[C*]`

| ID | Name | Default severity | When to flag |
|---|---|---|---|
| `[C1]` | Inappropriate information | MINOR | 주석에 changelog / 작성자 / 날짜. git이 안다. |
| `[C2]` | Obsolete comment | MINOR | 코드와 일치하지 않는 주석. |
| `[C3]` | Redundant comment | MINOR | `i += 1  # increment i`. |
| `[C4]` | Poorly written comment | MINOR | 무슨 뜻인지 불분명. |
| `[C5]` | Commented-out code | MAJOR | 주석 처리된 코드. 삭제. git이 안다. |

**Pet peeve**: changelog 주석(`[C1]`) — `# 2026-05-04 hyejin: fix flaky test`. 즉시 삭제 권장 (git이 이미 안다).

---

## Pipeline Reliability `[P*]`

CI/CD pipeline · GitHub Actions · build/test orchestration. **DevOps 시점에서 가장 자주 보는 영역.**

| ID | Name | Default severity | When to flag |
|---|---|---|---|
| `[P1]` | Silent failure mask | CRITICAL | `continue-on-error: true`, `<code>\|\| true</code>`, `set +e` — 실패가 green으로 보임. 명확한 사유 없이 사용 시. |
| `[P2]` | Missing job timeout | MAJOR | `timeout-minutes` 누락 → runner 영구 점유 위험. |
| `[P3]` | Non-idempotent step | MAJOR | re-run 시 다른 결과 (artifact 덮어쓰기 충돌, side effect 누적). |
| `[P4]` | Hard-coded host/path | MAJOR | `/home/runner/...`, hostname literal — 자체호스트 runner 환경 의존. |
| `[P5]` | Cache key incorrect | MAJOR | lockfile 변경 추적 누락. cache poisoning 또는 stale build. |
| `[P6]` | Missing retry on transient | MINOR (MAJOR if external API) | network/registry 호출에 재시도 없음. |
| `[P7]` | Retry without idempotency | CRITICAL | `[P6]`을 fix하면서 idempotent하지 않은 step에 retry 붙임. duplicate side effect. |
| `[P8]` | Build time budget regression | MAJOR | 단일 변경으로 빌드 시간 ≥ 20% 증가 (의도 없이). |
| `[P9]` | Required check bypass | CRITICAL | branch protection 우회 / `force-merge` / required job 비활성화. |
| `[P10]` | Concurrent job race | MAJOR | 동일 자원에 대한 `concurrency:` group 누락. shared state corruption. |
| `[P11]` | Step output as state | MAJOR | step output에 sensitive 또는 multi-line state를 담아 전달. |
| `[P12]` | Missing strict shell flags | MAJOR | CI/runner script가 `set -euo pipefail` 없이 실행. 중간 실패가 silent하게 다음 step으로 흘러감. |
| `[P13]` | GHA matrix fail-fast misuse | MAJOR | `fail-fast: true` (default) on regression matrix → 한 NPU device 실패가 전 fleet job 취소. 반대로 release-blocking pipeline에 `fail-fast: false`로 두면 첫 실패 신호 묻힘. context에 맞게 명시. |
| `[P14]` | `needs:` graph incorrectness | MAJOR | downstream job의 `needs:` 누락 / 잘못된 의존 → upstream 실패에도 downstream 실행, 또는 race로 stale artifact 사용. |
| `[P15]` | Serial await over N independent I/O calls | MAJOR | 서로 독립인 N개의 외부 호출(HTTP/SSH/DB)을 `for ... await` 루프로 순차 실행. per-call timeout이 T면 worst-case wall-clock이 N×T로 늘어 — daily 배치/핸들러가 부분 outage에 분 단위로 묶인다. 독립 호출은 bounded concurrency(`asyncio.Semaphore` + `gather`, 보통 10–20 in-flight)로 병렬화하고 실패는 개별 필터. 순서가 필요하면 `gather`가 입력 순서를 보존. |

**Pet peeve**:
- `<code>\|\| true</code>` 가 보이면 무조건 일단 CRITICAL 후보. 정당한 사유(known-flaky를 임시 격리 등) 없으면 머지 금지.
- timeout 없는 job은 daily regression이 목요일 새벽 3시에 멈춰도 누가 모름 → `[P2]` MAJOR.
- `set -euo pipefail` 빠진 bash step은 중간 실패가 묻힌다 → `[P12]`. multi-command step일수록 가시성 손실 큼.
- `[P15]` — `for id in ids: await fetch(id)` 패턴. 50개면 한 개만 느려도 전체가 직렬화된다. unbounded `gather`는 외부 API를 때리므로 semaphore로 in-flight 상한.

---

## Test Determinism `[T*]`

| ID | Name | Default severity | When to flag |
|---|---|---|---|
| `[T1]` | Flake-by-retry | CRITICAL | flaky test에 `@retry` / `pytest-rerunfailures` 도배. **Root cause 회피**. |
| `[T2]` | Time-based assertion | MAJOR | `time.sleep`, `datetime.now()` 비교 — FakeClock 사용. |
| `[T3]` | Network in unit test | MAJOR | unit test가 외부 호출. integration으로 격리 또는 fake 사용. |
| `[T4]` | Shared mutable fixture | MAJOR | 테스트 간 순서 의존. `pytest-randomly` 적용 시 깨짐. |
| `[T5]` | Coverage drop without note | MAJOR | 커버리지 하락 + PR 설명 없음. |
| `[T6]` | "Add tests later" promise | MAJOR | 구현만 들어오고 테스트 후속 약속. **거의 안 옴.** 같은 PR에 요구. |
| `[T7]` | Assertion without failing scenario | MINOR | `assert result is not None`만 — 의미 있는 invariant 검증 X. |
| `[T8]` | Unmarked integration test | MINOR | `-m integration` 마커 누락. unit run에 섞임. |
| `[T20]` | Robot Then SKIP-as-pass | MAJOR | RF `The test result SHOULD BE PASS` 류 키워드가 외부 러너(gtest, rsd_multi 등)의 SKIPPED를 `builtin.skip()`으로 통과시킴. PASS-only 정책 위반 — `builtin.fail()` + "check FW for the underlying cause" 메시지 권장. |
| `[T21]` | Stale skip-when test | MINOR | SKIP→FAIL 정책 변경 시 기존 `test_skip_when_*` 케이스를 `test_fail_when_*`로 의도 갱신 안 함. |
| `[T22]` | Unmocked time.sleep | MAJOR | unit test에서 `time.sleep` 호출이 mock 안 됨 → CI 시간 budget 직격. `FakeClock` 또는 `monkeypatch.setattr(time, "sleep", lambda _: None)`. |
| `[T23]` | Framework-mismatched mock | MAJOR | mock이 실제 프레임워크 동작과 불일치 — Fabric `warn=False` 기본값, non-zero exit → `UnexpectedExit` raise 등. `warn=True` 사용 시 `Result.ok` 체크 누락도 포함. |

**Pet peeve**:
- `[T1]` retry 도배 → daily regression 신뢰도 무너뜨림. 해결책은 root cause fix 아니면 quarantine + ticket.
- `[T6]` "나중에 테스트" → 절대 안 온다. 이번 PR에서 요구.
- `[T20]` Robot Then SKIP-as-pass → 진짜 회귀가 CI 게이트를 통과해 묻히는 1순위 원인. ssw-bundle test/system 직격.
- `[T22]` time.sleep 미mock → CI 6시간 한도에서 누적되면 daily regression 정지.

---

## Secret & Runner Safety `[S*]`

| ID | Name | Default severity | When to flag |
|---|---|---|---|
| `[S1]` | Hard-coded secret | CRITICAL | API key / token / password literal. |
| `[S2]` | Secret in step output / log | CRITICAL | `echo $TOKEN`, `set -x`로 secret이 log에 노출. |
| `[S3]` | Secret in artifact | CRITICAL | upload된 artifact에 secret 포함 (build output, dump 등). |
| `[S4]` | Missing redaction | MAJOR | log 출력에 redaction processor 미적용. |
| `[S5]` | Self-hosted runner trust assumption | MAJOR | fork PR이 self-hosted runner에서 실행되는 경로. |
| `[S6]` | Secret over-scope | MAJOR | repo-level secret이면 충분한 곳에 org-level secret 사용. |
| `[S7]` | Secret rotation absent | MINOR | 만료 정책 / rotation 절차 없음. |
| `[S8]` | env-leaked secret | MAJOR | OAuth/token이 startup 후 `os.environ`에 잔존 (`--insecure-env` 외). |
| `[S9]` | Untrusted external input parsed unbounded | MAJOR | 네트워크/원격 입력을 hardening·크기 제한 없이 파싱. XML(`xml.etree`, `lxml`) → XXE / billion-laughs / 깊은 중첩 DoS (Bandit S314), 거대 JSON/YAML → 메모리 폭발, 무한 정규식 → ReDoS. 새 의존성(`defusedxml`)이 과하면 최소한 파싱 전 byte cap. "trusted feed"라는 라벨은 근거가 아니다 — 원격이면 untrusted (`[D26]`와 함께 잡힘). |

**Pet peeve**:
- `[S2]` — `set -x`나 `--verbose` 모드에서 secret이 노출되는 경우. CI debug 시도가 secret leak으로 직결.
- `[S9]` — `# noqa: S314 — trusted feed`로 끈 원격 XML 파싱. stdlib `ElementTree`는 외부 엔티티는 막지만 거대/중첩 문서 CPU 고갈은 여전. byte cap이 dep 없는 최소 방어.

---

## Observability `[O*]`

"이게 daily regression에서 꺼지면 누가, 어떻게 알지?"가 답이 안 나오면 여기.

| ID | Name | Default severity | When to flag |
|---|---|---|---|
| `[O1]` | Silent error path | CRITICAL | error에 log/metric/alert 없음. 사용자가 "왜 안 되지?" 만 알 수 있음. |
| `[O2]` | Unstructured log | MAJOR | `_log.info(f"Failed for {id}")` — Loki/Grafana 쿼리 어려움. structured field로. |
| `[O3]` | Missing correlation id | MAJOR | 요청/이벤트 흐름 추적 불가. |
| `[O4]` | No metric on critical path | MAJOR | regression 통과율 / pipeline 성공률 등 핵심 KPI 미수집. |
| `[O5]` | No alert on critical path | MAJOR | 메트릭은 있으나 alert 미연결. **모니터링 ≠ 알림**. |
| `[O6]` | Log volume regression | MINOR | per-event log가 N배 증가 → 비용 / 노이즈. |
| `[O7]` | PII / secret in log | CRITICAL | redaction 미통과 필드. (`[S4]`와 함께 잡힘) |
| `[O8]` | Retention shorter than on-call window | MAJOR | artifact / CI log / Loki retention이 on-call 응답 시간보다 짧음. 새벽에 실패한 regression이 출근 전에 사라짐 → root cause 추적 불가. retention ≥ on-call rotation 주기. |
| `[O9]` | Log value disagrees with enforced value | MINOR | 가드/임계값은 X 단위로 판정하는데 로그는 Y 단위/다른 변수로 기록. 예: `len(body.encode())`로 byte 한도를 enforce하면서 `bytes=len(body)`(char count)로 로깅 → 한글 입력에서 byte≠char라 진단이 어긋남. 임계값 위반 로그는 **실제로 enforce한 그 값**(+한도)을 찍어야 한다. |

**Pet peeve**:
- `[O9]` — limit 위반 로그에 enforce한 값과 다른 단위를 찍으면, 알람 받은 on-call이 "왜 이게 한도를 넘었지?"를 로그만으로 못 푼다. 측정·판정·로깅 세 군데가 같은 값이어야.
- `[O1]` — "에러는 났는데 silent로 swallow하고 retry" → 100번째 retry까지 아무도 모름. CRITICAL.
- `[O2]` — Loki에서 검색되지 않는 로그는 없는 거나 마찬가지.

---

## Infrastructure-as-Code `[I*]`

Terraform · K8s manifests · Helm · Ansible · GitHub Actions YAML · launchd / systemd unit.

| ID | Name | Default severity | When to flag |
|---|---|---|---|
| `[I1]` | Drift between IaC and live | MAJOR | code 변경 없이 live 상태가 다름 (직접 수정). |
| `[I2]` | Non-additive migration | CRITICAL | 기존 마이그레이션 in-place 수정. linear/additive 원칙 위반. |
| `[I3]` | Missing rollback path | MAJOR | rollout만 있고 rollback 절차 없음. |
| `[I4]` | Missing health/readiness | MAJOR | K8s probe 누락 → traffic이 not-ready pod로. |
| `[I5]` | Resource limits missing | MAJOR | container `limits` 없음 → noisy neighbor. |
| `[I6]` | RBAC over-grant | MAJOR | namespace 충분한데 cluster role. |
| `[I7]` | Hard-coded environment | MAJOR | env-specific value가 manifest에 직접. Helm/values 분리. |
| `[I8]` | No diff-preview gate | MINOR | `terraform plan` 결과를 PR에 노출하지 않음. |

**Pet peeve**: `[I2]` — daily regression DB의 마이그레이션이 in-place 수정되면 환경 간 schema 불일치로 며칠 뒤 폭발.

---

## NPU Lab `[L*]`

NPU 하드웨어 fleet · 공유 자원 · regression scheduler.

| ID | Name | Default severity | When to flag |
|---|---|---|---|
| `[L1]` | Missing device lock/queue | CRITICAL | 같은 NPU device를 여러 job이 동시 점유. corruption / flake. |
| `[L2]` | Device leak | MAJOR | test 종료 시 자원 해제 안 됨 (open file / mmap / shared memory). |
| `[L3]` | Lab assumption hard-coded | MAJOR | `/dev/rebel0` 같은 specific device path. fleet에서 안 통함. |
| `[L4]` | No quarantine path | MAJOR | flaky/broken device를 격리할 수단 없음 → 다음 job에서도 깨짐. |
| `[L5]` | Firmware/driver version assumption | MAJOR | 특정 FW/KMD 버전에서만 동작. version probe 없음. |
| `[L6]` | Long-running test without checkpoint | MINOR | regression test가 6시간인데 중간 진행상황/재개점 없음. |

**Pet peeve**: `[L1]` lock 누락 → 두 job이 같은 device 잡으면 둘 다 fail. retry로 가려져서 며칠씩 silent.

---

## Clean Architecture `[A*]`

Dependency rule / boundary / framework-as-plugin 위반은 코드 자체가 작동해도 운영 단계에서 변경 비용을 폭증시킨다. CI/CD·infra 코드도 예외 아님 — handler가 외부 framework 타입을 반환하거나, infra adapter가 도메인 룰을 알면 부서 간 이관·테스트 격리가 깨진다.

| ID | Name | Default severity | When to flag |
|---|---|---|---|
| `[A1]` | Dependency rule reversal | MAJOR | 내층(core / domain)이 외층(infra / framework)에 의존. import 화살표가 잘못된 방향. 예: `core/events.py` 가 `httpx` 또는 `aiosqlite` import. |
| `[A2]` | Framework type leak across boundary | MAJOR | handler가 framework-bound 타입(예: `httpx.Response`, `aiosqlite.Row`)을 caller에 반환. 도메인 타입으로 변환 후 반환해야 swappable. |
| `[A3]` | Boundary interface 누락 | MINOR | infra adapter에 Protocol/ABC 없이 concrete 클래스 직접 의존. test fake 주입 불가 — 우리 secrets/claude 패턴(`SecretsProvider` Protocol)과 대조. |
| `[A4]` | Use-case 책임 혼재 | MAJOR | 하나의 handler가 (a) 도메인 결정, (b) infra I/O, (c) 외부 API 호출, (d) 응답 렌더링을 다 함. 4개 단계가 같은 함수에 묶이면 단위 테스트가 mock 4종을 동시에 잡아야 함 — 책임 분리 필요. |
| `[A5]` | Circular import / 양방향 의존 | MINOR | 두 모듈이 서로 import. 분리할 공통 추상화 (`core/` 로 빼기)가 누락된 신호. |
| `[A6]` | Producer wired independently of its consumer | MAJOR | event producer(trigger/cron/publisher)를 consumer(handler/subscriber)와 따로 배선해서, 한쪽만 enable되거나 dep이 빠진 partial-config에서 **아무도 소비 못 하는 이벤트를 계속 발행**한다. 결과는 dispatcher dead-letter / unrouted-event 무한 누적 — 실제 misconfig가 noise에 묻힌다. composition root에서 producer는 "target consumer가 실제로 등록됐는가"를 확인하고 배선해야 한다. (cron이 미등록 핸들러로 fire → dead-letter spam이 대표 사례.) **게이트는 resolved 실제 상태(빌드된 registry)로 판정** — config-driven target(`[triggers.cron].handler`)을 하드코딩 가정(`{"news"}`)으로 게이트하면, 그 가정을 벗어난 target(예: `echo`)에서 게이트가 틀려 정상 배선을 막는다. authoritative source(registry)에서 wired set을 도출하라. |

**Pet peeve**:
- `[A2]` framework type leak — 한 번 caller가 `httpx.Response` 를 직접 만지기 시작하면 adapter 교체 시 caller까지 다 수정해야 한다. handler 시그니처가 framework-free한지가 첫 신호.
- `[A6]` producer/consumer 독립 배선 — "trigger는 켜졌는데 handler dep이 None이라 skip" 같은 부분 활성화가 가장 흔한 트리거. dead-letter는 조용히 쌓이다 retention에 밀려 사라지므로(`[O8]`), 발행 전에 consumer 등록 여부를 게이트하는 게 정답.

---

## Spec / Process Drift `[D*]`

| ID | Name | Default severity | When to flag |
|---|---|---|---|
| `[D1]` | Implementation without spec update | MAJOR | 동작이 바뀌었는데 PLAN.md / spec / RFC 미수정. |
| `[D2]` | Spec without test | MAJOR | spec에 명시된 동작인데 검증 테스트 없음. |
| `[D3]` | Verify step removal | CRITICAL | "왜 verify 날렸어?" — 검증 단계를 사유 없이 제거. |
| `[D4]` | Bypassed required review | CRITICAL | review-required path 우회 (force push / squash 후 review 손실). |
| `[D5]` | Hidden behavior change in refactor | MAJOR | "refactor" PR인데 동작 변경 동반. |
| `[D6]` | Documented runbook untouched | MINOR | 운영 절차 변경 동반인데 runbook 미갱신. |
| `[D20]` | `Co-Authored-By:` trailer present (**rebellions-sw ONLY**) | MAJOR | `rebellions-sw/*` 레포 commit message에 `Co-Authored-By:` 트레일러 포함 → checkpatch warning. 다른 owner의 레포에서는 발행하지 말 것 (해당 정책은 rebellions-sw 전용). AI co-author 자동 추가 도구 사용 시 자주 발생. 제거 + `git commit --amend` 후 force-push. |
| `[D21]` | Missing `Signed-off-by:` | MAJOR | DCO sign-off 누락. `git commit -s` 누적 습관. |
| `[D22]` | Convention summary cited instead of source | MAJOR | `inv/`, `.github/workflows/`, `test/system/` 변경 시 에이전트 요약·간접 인용으로 컨벤션을 다룸. `docs/conventions/invoke.md` §1 echo=True / §5 단위 테스트 필수 등이 누락된 채로 코드가 들어옴. 원본을 직접 Read 한 후 작업해야 함. 기존 코드의 컨벤션 위반을 그대로 복사한 PR도 동일 등급. |
| `[D23]` | Release PR base mismatch | CRITICAL | default branch가 `dev`인 레포에서 `release/x.x` 타겟 PR이 `--base` 누락으로 인해 base가 `dev`로 잡혀 생성됨. base/head 확인 (`gh pr view <n> --json baseRefName,headRefName`) 후 close + 재생성. |
| `[D24]` | Backport dependency without host validation | MAJOR | `dev → release/v3.x` cherry-pick 충돌 풀기 위해 의존 헬퍼를 함께 backport했지만, target deploy host에서 실측한 증거(`sshpass`로 `sudo rbln product_info` 같은 1줄 명령 출력)가 **PR description · commit 메시지 · 또는 PR 코멘트 thread** 어디든 없음. 헬퍼가 실제로는 불필요해서 호출 대체로 우회 가능한 경우가 흔함. prereq 의존이 진짜 필요하면 별도 PR로 분리하고 그 commit/티켓 참조 명시. |
| `[D26]` | PR-internal asymmetric pair drift | MAJOR | 같은 PR에서 짝 동사(reserve/unreserve · add/remove · install/uninstall · setup/teardown · enable/disable · acquire/release · lock/unlock · on/off · open/close)의 인자·옵션·로깅·에러 핸들링이 한쪽에만 적용됨. 예시: `reserve`는 `echo=True`인데 `unreserve`는 누락 (`inv/test_pipeline/test_pipeline.py:177` 류), `install`은 `continue-on-error: true`인데 `uninstall`은 hard fail, lock은 timeout 있는데 unlock은 없음. **검사**: PR diff에서 동사쌍을 grep으로 묶고 한쪽 hunk의 keyword가 반대쪽에도 있는지 대조. 의도적 비대칭은 주석으로 명시되어 있어야 함. |
| `[D25]` | Docstring/contract contradicts behavior | MAJOR | docstring·주석·타입 계약·`<=N` 보장 문구가 실제 구현과 어긋남. 예: "missing a day is logged"인데 로그 없음, "returns <=4000 chars"인데 초과 경로 존재, "within last 24h"인데 미파싱 항목 통과. **두 아티팩트가 같은 값을 약속해야 하는데 서로 다른 한도를 말하는 경우도 포함** — 특히 LLM 프롬프트가 `<=40 chars`라는데 pydantic validator는 `max_length=120`, 또는 주석의 예시 수치(`00:00 UTC`)가 실제 상수(`23:45 UTC`)와 불일치. 둘 중 하나를 진실로 — 동작/한쪽을 맞추거나 문구를 고쳐라. (코드와 안 맞는 *주석* 일반은 `[C2]`; 명시적 **계약/보장**의 거짓은 `[D25]` — 호출자/모델이 그 보장에 의존하므로 더 무겁다.) |
| `[D26]` | Static-analysis suppression without mitigation | MAJOR | `# noqa: <rule>` / `# nosec` / Bandit `# type: ignore`로 경고를 끄면서 근거 주석이 사실과 다르거나 실제 완화가 없음. 예: 네트워크 입력에 `# noqa: S314 — trusted feed`(신뢰 아님), 명백한 버그에 `# type: ignore`. suppression은 "왜 안전한가"를 1줄로 입증하거나 제거. 억압 자체보다 **거짓 사유**가 위험 — 다음 사람이 진짜 안전한 줄 안다. |

**Pet peeve**:
- `[D3]` — verify를 우회하는 패턴은 daily regression의 신뢰 기반을 직접 무너뜨린다. 사유 + 대체 검증 없으면 CRITICAL.
- `[D20]` Co-Authored-By → ssw-bundle CI의 checkpatch에서 직접 warning을 띄움. 자동 도구가 추가했으면 즉시 amend.
- `[D22]` "컨벤션 요약 인용" → 한 번 통과시키면 같은 위반이 옆 PR에 복사된다. 원본 Read 흔적이 commit / PR description / 리뷰 thread에 없으면 강제.
- `[D23]` `--base` 누락 PR → 잘못된 base로 머지되면 release line이 dev 변경을 통째로 가져옴. 발견 즉시 close.
- `[D25]` "logged"·"<=N"·"validated" 같은 보장 문구는 호출자가 그대로 믿는다. 리뷰 시 문구와 코드 경로를 한 번 더 대조 — 특히 새로 추가된 docstring.
- `[D26]` `noqa: S314 — trusted feed` 처럼 *근거가 곧 틀린* 억압이 가장 위험. 외부 입력에 trusted 라벨 붙이는 패턴 경계.

---

## How to add a rule

리뷰 중 카탈로그에 없는 안티패턴이 두 번 이상 나오면:
1. 사용자에게 보고: `"카탈로그에 없는 패턴 X — 추가할까요?"`
2. 승인 시 적절한 카테고리에 ID 추가 (`[P12]` 형태로 다음 번호).
3. severity / when-to-flag / pet peeve 한 줄.

룰은 **자라는 카탈로그**다. 리뷰 한 번이 카탈로그 한 줄로 남도록.
