# Output Format

리뷰 출력은 **이 형식 그대로**. 변형하지 말 것.

## Severity

| 라벨 | 기준 |
|---|---|
| 🚨 **CRITICAL** | 머지 시 production / daily regression / pipeline / secret / data 손실 즉시 위험. 회귀로 동작 깨짐. 핫픽스 없이 머지 금지. |
| ⚠️ **MAJOR** | 머지 가능하나 같은 PR 내 fix 권장. correctness 문제 / observability gap / 명백한 Clean Code 위반 / non-idempotent CI step. |
| 💡 **MINOR** | 카탈로그 룰(`[N*]`/`[F*]`/`[G*]`/`[C*]`) 매칭 + 운영 영향 한 가지 이상 (daily regression / runner / secret / build budget / flake / lab lock / rollback / drift 중 하나). 카탈로그 매칭만 있고 운영 영향이 없으면 **finding 아님** — drop. |

**MINOR 발행 게이트**: SKILL.md hard rule이 강제한다 — 단순 style·naming preference, 취향 문제, hypothetical risk("~할 수도 있음")로 MINOR를 발행하지 않는다. 의심스러우면 drop. 그 결과 finding=0이면 [Verdict](#verdict) = `APPROVE`. False-positive MINOR로 APPROVE를 깎지 말 것.

표기 규칙 (deterministic):
- **PR-bound 출력 (default)**: 본문 산문은 한국어, 라벨·룰 ID·`file:line`·코드 식별자·verdict 라벨은 ASCII 영어 토큰. 라벨 자체에는 이모지 금지(`CRITICAL` / `MAJOR` / `MINOR`, `PASS` / `CONCERNS` / `FAIL`) — PR 호스트별 렌더링 차이를 피한다.
- **채팅 토론**: 라벨 앞에 이모지 토큰 허용. `🚨 CRITICAL` / `⚠️ MAJOR` / `💡 MINOR`, `✅ PASS` / `⚠️ CONCERNS` / `❌ FAIL`. 한 출력 안에서는 한 가지 표기만.
- **사용자가 영어 본문을 명시 요청한 경우에만** PR-bound 본문도 영어. 그 외엔 한국어 default.
- 예외: PR-bound caller의 sign-off 줄(`— hyejin-bot 🐱✨`)은 ASCII-only 규칙에서 의도적으로 제외 — 봇 식별 마커. [delivery.md §Sign-off](delivery.md#sign-off) 참조.

## Verdict

| 라벨 | 기준 | GH event |
|---|---|---|
| 🟢 **APPROVE** | finding 0개 (CRITICAL 0 / MAJOR 0 / MINOR 0). | `APPROVE` — branch protection 카운트에 포함됨 |
| ✅ **PASS** | CRITICAL 0개, MAJOR 0개. MINOR ≥ 1. | `COMMENT` |
| ⚠️ **CONCERNS** | CRITICAL 0개, MAJOR ≥ 1개. 같은 PR에서 fix 후 머지. | `COMMENT` |
| ❌ **FAIL** | CRITICAL ≥ 1개. 머지 금지. fix 후 재리뷰. | `COMMENT` |

`APPROVE`는 **진짜 GitHub APPROVE 이벤트**다 — hyejin 계정으로 기록되며 branch protection 승인 카운트에 잡힌다. JSON `verdict` 필드와 `comments[]` 의 정합성이 schema validator로 강제된다: `verdict=APPROVE`이면 `comments==[]` 여야 한다 (validator가 reject). 즉 inline finding이 한 개라도 있으면 APPROVE 못 한다.

Verdict 라인 형식: `**Verdict**: <APPROVE | PASS | CONCERNS | FAIL> — <한 문장 근거>`. 본문 첫 줄에 위치하되, role-primed인 경우 `**Reviewer**: as Senior <Role>` 한 줄이 그 위에 들어간다. 근거는 별도 섹션이 아니라 같은 줄에 통합 — 별도 Recommendation Rationale 섹션을 두지 않는다.

## Review Summary 템플릿

PR-bound 출력의 표준 형태. **본문 산문은 한국어**, 라벨·룰 ID·`file:line`·코드 식별자는 영어 ASCII 유지. 채팅 caller는 이모지/sign-off 정책만 다름 ([delivery.md](delivery.md) §Caller modes).

### Summary 본문 분량 (HARD)

- **Target ≤ 1500자, hard cap 2500자.** 초과 시 Pydantic validation 실패 → 1회 재시도 → DeadLetter.
- **개요 단락이 본문의 절반 이상**이어야 한다. Findings 표가 본문을 점령하면 슬림화 실패 — finding 산문은 inline으로 옮겨라.
- 허용 섹션만: (옵션) Reviewer 라인, Verdict 라인, 개요, Findings 표(N≤6 평면 표 / N>6 `<details open>` 로 감싸기), Positive(0–2 bullets, 없으면 생략), sign-off.
- **금지**: Detail 산문(`### N. [SEV] ...`), 코드 펜스, 멀티문장 표 셀.
- **Body invariant**: sign-off (`— hyejin-bot 🐱✨`) **이후로는 어떤 산문도 오지 않는다**. Positive 다음 줄에 `— hyejin-bot 🐱✨`, 그게 본문의 마지막 non-empty 라인. 그 뒤에 `- [filename near LN] [MAJOR] ...` 같은 패턴이 보이면 InlineComment가 잘못 본문으로 흘러들어온 구조 오류 — InlineComment는 `comments[]`로만 발행. body markdown 안에 `[SEVERITY] file:line — ...` 형태가 또 나오면 그 finding은 표에서 중복 발행된 것이므로 즉시 제거.

### Findings 표 분량 처리

**모든 finding의 evidence·fix 산문은 `comments[]`의 InlineComment로**. 본문 표는 한 줄 요약만. CRITICAL/MAJOR는 inline 필수, MINOR는 권장(강제 X).

| Findings 총 N | 출력 방식 |
|---|---|
| **N ≤ 6** | 평면 표 그대로. 표에 모든 행, Detail 산문 없음. |
| **6 < N ≤ 30** | 표 전체를 `<details open><summary><b>Findings: N CRITICAL / M MAJOR / K MINOR</b></summary> ... </details>` 로 감싸기 (default expanded — 사용자가 접고 싶으면 토글 가능). severity 순 정렬(CRITICAL → MAJOR → MINOR). |
| **N > 30, CRITICAL ≤ 15** | `<details open>` 안에 상단 15개(severity desc) + `…and <N-15> more (see Appendix)` 한 줄. 나머지는 같은 형식 Appendix 섹션으로. CRITICAL은 *전부* 본문에 — Appendix로 밀지 말 것. |
| **N > 30, CRITICAL > 15** | 본문 표 = **CRITICAL 전부** (15-row cap 무시). MAJOR/MINOR는 전부 Appendix로. CRITICAL "all-in-main" 원칙이 15-row cap을 항상 이긴다. |

**Rule column convention**: catalog ID(`[G35]`, `[P1]`, …) 가 매칭되면 그 ID를. 매칭 룰이 없으면 `—` (em dash). `—` 행도 SKILL.md hard-rule을 통과해야 한다 — 평문 룰 서술 + `file:line` 앵커 + fix hint 필수.

**설명 셀 규칙**: ≤ 80자, 한 줄, 줄바꿈/코드 펜스/멀티문장 금지. Evidence·fix는 inline으로.

### 템플릿 (PR-bound, default role, N ≤ 6)

```
**Verdict**: <PASS | CONCERNS | FAIL> — <한 문장 근거>

**개요**
<2–3문장 한국어. 이 PR이 무엇을 바꾸는지, 누구에게 영향이 가는지, 주요 위험 표면.
finding 나열 금지 — walkthrough 성격의 단락.>

| # | Severity | File:Line | Rule | 설명 |
|---|----------|-----------|------|------|
| 1 | CRITICAL | path/to/file.py:42 | [G35] | 예외 무시 — daily-regression flake 원인. |
| 2 | CRITICAL | .github/workflows/ci.yml:88 | [P1] | `continue-on-error: true` 가 unit-test 실패 가림. |
| 3 | MAJOR    | .github/workflows/ci.yml:14 | [P2] | `timeout-minutes` 누락 — runner 무한 보유 가능. |
| 4 | MAJOR    | scripts/release.sh:23 | — | rollback 경로 부재 — forward path만 문서화. |
| 5 | MINOR    | scripts/deploy.sh:12 | [N3] | `tmp` → `release_artifact_dir` 권장. |

**Positive**
- `scripts/migrate.py` migration이 idempotent — 재실행 가능.
- `_log.error` 가 structured field 포함 — Loki query 친화.

— hyejin-bot 🐱✨
```

**N > 6 변형**: 위 표를 통째로 다음으로 감싼다 — `<details open><summary><b>Findings: N CRITICAL / M MAJOR / K MINOR</b></summary>` ... `</details>`. default-expanded 이므로 사용자가 토글로 접을 수 있다.

**Role-primed 변형**: Verdict 위에 `**Reviewer**: as Senior <Role>` 한 줄 추가, sign-off도 `— hyejin-bot 🐱✨ (as Senior <Role>)`.

Findings 0개면 표 자체 생략. 두 경우로 나뉜다:
- **`Verdict: APPROVE`** (deserving approval): Verdict + 개요 + (옵션) Positive + sign-off. 봇이 실제 GitHub APPROVE 이벤트를 emit하므로, 정말 finding이 0개인 케이스만. 추측성 "approve 가능해 보임" 으로 가지 말 것.
- **`Verdict: PASS`** (MINOR-only): MINOR finding이 있는데 카탈로그 룰을 통과한 케이스. CRITICAL/MAJOR 0개라 GH event는 COMMENT.

PR-bound 본문에서는 verdict 라벨 앞에 이모지를 붙이지 않는다(예: `✅ PASS` 금지, `PASS` 만 사용) — 채팅 caller에서만 이모지 토큰 허용.

### Sign-off (필수)

PR-bound 출력은 본문 **마지막 줄**에 빈 줄 하나 띄우고:

```
— hyejin-bot 🐱✨
```

Role-primed: `— hyejin-bot 🐱✨ (as Senior SRE)` 처럼 괄호 첨가. 누락 시 봇 식별 불가 — [delivery.md §Sign-off](delivery.md#sign-off) 가 SoT.

## Inline comment 형식

본문 Findings 표의 모든 산문(evidence·fix·실패 시나리오)이 **여기로** 간다. 본문은 한 줄, inline은 multi-line + code fence 허용.

**필수 vs 선택**:
- CRITICAL / MAJOR finding → InlineComment 1개 **필수**.
- MINOR → 권장 (강제 X).

**기본 한 줄 형태** (간단한 finding):

```
[CRITICAL] path/to/file.py:42 — ConnectionError 무음 처리. 좁혀잡고 structured field로 로깅 권장 — daily-regression flake 원인.
```

**확장 형태** (evidence + fix가 길 때):

```
[CRITICAL] path/to/file.py:42 — 예외 무시로 telemetry 실패가 silently 가려짐.

```python
# offending
try:
    fetch_telemetry()
except Exception:
    pass
```

수정: `ConnectionError` 로 좁히고 `_log.error("telemetry.fetch_failed", err=...)` 로 logging,
재시도 경로로 propagate. daily-regression이 깨지면 이 줄에서 시작된다.
```

규칙:
- 첫 줄은 항상 `[SEVERITY] file:line — 한국어 한 문장.` 마침표 포함.
- **bullet dash marker `-` 금지** (inline 코멘트는 다중 bullet 리스트가 아니라 한 finding의 evidence/fix 본체).
- **filepath 중복 금지** — `[file.sh near L142] [MAJOR] file.sh:142 —` 같은 패턴 X. 한 번만 박는다: `[MAJOR] file.sh:142 —`.
- **`near` / `around` 모호한 범위 표현 금지** — 정확한 라인 번호만. 다중 라인이면 `file.sh:142-148` 처럼 hyphen range.
- 한국어 산문 + ASCII 라벨/`file:line`/룰 ID/코드 식별자.
- 사용자가 영어 출력 명시 요청한 경우에만 영어.
- 한 inline 안에서 같은 finding의 evidence + fix를 묶는다 — 산문을 본문 표로 다시 보내지 말 것.
- **InlineComment는 `comments[]` array로만 전달**. 메인 review body (markdown) 에는 절대 인라인 코멘트 산문을 다시 적지 않는다. body는 Verdict + 개요 + Findings 표 (한 줄 설명) + Positive + Sign-off로 끝난다 — 그 사이에 `[MAJOR] file:line — ...` 류 평문이 보이면 구조 오류.

## Pushback 처리

사용자가 다음과 같이 push back할 때:
- `"뭐가 문제라는거야?"`
- `"Critical 부터 자세히 뭐가 문제인지 설명해줘"`
- `"어떤것들이 문제였는지 하나씩"`

대응 — 직전 Verdict에 따라:

| 직전 Verdict | 강조 대상 |
|---|---|
| ❌ FAIL (CRITICAL ≥ 1) | **CRITICAL만** 실패 시나리오 동반 재설명. MAJOR는 한 줄 요약, MINOR 전부 제거. |
| ⚠️ CONCERNS (CRITICAL 0 / MAJOR ≥ 1) | **상위 MAJOR 1–3개**를 실패 시나리오 동반 재설명. 나머지 MAJOR는 한 줄, MINOR 전부 제거. |
| ✅ PASS (CRITICAL/MAJOR 0) | finding이 없으니 실패 시나리오를 만들지 않는다. "PASS였습니다 — 어떤 차원을 더 보길 원하시나요? (예: pipeline reliability, security, test determinism)" 하고 묻는다. |

실패 시나리오 형식 — "이게 머지되면 [어느 단계 / 어떤 트리거]에서 [어떤 식으로 깨지는가]. [관측되는 증상]." 한국어 OK, 길어도 OK.

Verdict 자체는 push back 한 번으로 바뀌지 않는다 — 새 정보가 들어와야 바뀐다.

## Re-review (`다시 리뷰`)

이전 리뷰 이후 변경이 있으면:
- **Resolved** 섹션 추가 — 직전 finding 중 해결된 항목, 1줄씩.
- **Still open** — 미해결 finding 재기재 (severity 유지).
- **New** — 새로 발견된 finding.

## Review-of-reviews (`리뷰 코멘트 검토 바로 고치지말고`)

다른 reviewer의 finding을 받아서 판정만:

```
## Reviewer Audit

| # | Their Severity | Verdict | Comment |
|---|---------------|---------|---------|
| 1 | CRITICAL | ✅ Agree | Genuine flake source. Fix before merge. |
| 2 | MAJOR    | ✅ Agree-with-correction | Right issue, but the failing line is `auth.py:88`, not `auth.py:42`. Severity stands. |
| 3 | MAJOR    | ⚠️ Downgrade to MINOR | Cosmetic; not a correctness issue. |
| 4 | MINOR    | ❌ Disagree | This is actually MAJOR — secrets in step output. |

Verdict 옵션:
- `✅ Agree` — finding · severity · 위치 모두 동의.
- `✅ Agree-with-correction` — finding 자체는 맞으나 file:line / severity 한 가지가 틀림. 어떤 부분을 정정하는지 명시.
- `⚠️ Downgrade to <SEV>` / `⚠️ Upgrade to <SEV>` — finding 인정하나 severity 변경.
- `❌ Disagree` — finding 자체 거부.

**Net delta**: 1 upheld, 1 corrected, 1 downgraded, 1 upgraded. Their verdict was directionally correct but mis-prioritized and one line reference wrong.
```

코드는 손대지 않는다.

## Role priming {#role-priming}

`"[role] 입장에서 리뷰해줘"` 패턴.

- **Default = Senior DevOps Engineer** — 사용자가 role을 지정하지 않으면 이 페르소나가 default. `**Reviewer**:` 라인을 *생략*한다 (이미 known). Sign-off도 괄호 첨가 없이 `— hyejin-bot 🐱✨`.
- **Role을 지정한 경우 (default와 다름)** — Verdict 라인 **바로 위**에 `**Reviewer**: as Senior <Role>` 한 줄을 추가한다. 그 row의 차원을 Findings 표 정렬에서 위로 끌어올림. Sign-off도 `— hyejin-bot 🐱✨ (as Senior <Role>)` 로 변경.
- **Default를 다시 명시한 경우** ("DevOps 입장에서") — Reviewer 라인을 추가하지 않는다 (no-op). 사용자가 다른 페르소나에서 돌아왔음을 알리는 신호로만 처리.

| Role | 가장 먼저 보는 차원 |
|---|---|
| **Senior DevOps Engineer** *(default)* | pipeline reliability, runner safety, secret hygiene |
| **Senior SRE / Platform Engineer** | observability, rollback path, blast radius, on-call ergonomics |
| **Senior Build Engineer** | build cache correctness, artifact lifecycle, build time budget |
| **Senior Test Infrastructure Engineer** | test determinism, flake mitigation, NPU lab queue/lock |
| **Senior Release Engineer** | rollout strategy, feature flag, kill switch, version compatibility |
| **Senior Backend Engineer** | correctness, error handling, API contract, data integrity |
| **Senior DBA** | schema migration safety, index/lock, query plan, data backfill |

Role을 받으면 그 row의 차원을 Findings 표 정렬에서도 위로 끌어올린다. 다른 차원도 여전히 본다 — 무게중심만 이동.

---

## Dedup Mode (Rule 9) {#dedup-mode}

PR에 이미 올라와 있는 코멘트와 finding이 의미 중복일 때의 출력 명세. SKILL.md Rule 9 / Hard rule / Workflow §5 의 출력 contract.

### Match criteria (모두 충족 시 dedup)

| 기준 | 정의 |
|---|---|
| **C1. 룰 ID 일치** | 같은 카탈로그 룰 ID (예: 둘 다 `[G50]`) **또는** 같은 file path + same Clean Code chapter |
| **C2. 위치 근접** | `file:line` ±5 lines |
| **C3. Thread 상태** | open / unresolved 상태 (resolved/outdated/collapsed thread는 dedup 대상 아님 — 회귀 가능성) |

C1·C2·C3 모두 충족하지 않으면 dedup 하지 말고 새 finding으로 정상 발행.

### 출력 분기 (CONFIRM vs REFINE)

| 케이스 | 어떤 reply | 본문 |
|---|---|---|
| Finding이 기존 코멘트와 동일 결론·동일 root cause | `[CONFIRM]` | 한 줄 동의 + (옵션) 한 줄 보강 사실. 예: `[CONFIRM] 동의. 같은 룰 [G50] — guard 4중첩 패턴.` |
| Finding이 같은 root cause인데 missing context (인접 라인·convention §·실측 evidence) 추가할 게 있음 | `[REFINE]` | 한 줄 추가 정보. 예: `[REFINE] 같은 패턴이 inv/test_pipeline/test_pipeline.py:850에도 있음.` |

Reply 본문은 **한 줄**. 별도 Verdict / 표 / sign-off 없음 (sign-off는 main review body의 마지막에만).

### Main review body 표기

dedup된 finding은 Findings 표에 row를 적지 않는다. 그 대신 개요(섹션 끝부분) 마지막 줄에:

```
Co-signed: <thread-url-1> ×1, <thread-url-2> ×1
```

또는 같은 thread에 여러 finding이 dedup되면:

```
Co-signed: <thread-url-1> ×3
```

`×N` 의 N은 dedup된 finding 수 (×1이면 생략 가능).

### Caller contract (운영 측 책임)

- **현재 운영 한계**: handler는 `gh.list_prior_reviews_with_comments(login=self.github_username)` 로 자기 이전 리뷰만 fetch. 인간 reviewer · Copilot · daeyeon-bot 코멘트 dedup은 handler가 다음 3-tuple을 모두 fetch하도록 확장된 후 가능:
  - `GET /repos/{owner}/{repo}/pulls/{n}/comments` — review_comments (file:line-bound, dedup 주 대상)
  - `GET /repos/{owner}/{repo}/issues/{n}/comments` — issue_comments (PR 본문에 달린 비-인라인 코멘트, dedup은 content similarity로만)
  - `GET /repos/{owner}/{repo}/pulls/{n}/reviews` — pull_request_reviews (review body 자체)
- **Persona 책임**: dedup 판정 + `[CONFIRM]`/`[REFINE]` reply 본문 + `Co-signed:` 라인 생성.
- **Caller 책임**: thread URL을 review_comments API `in_reply_to_id` 로 변환 후 reply 발행. main review body는 `gh api ...reviews POST` 로 정상 발행.

### Example — main body with dedup

```
**Verdict**: CONCERNS — `inv/test_pipeline/test_pipeline.py:850` AND-분배 OR-단일토큰 시뮬레이션 누락.

개요. dryrun 시 `_compose_include`의 의미가 production filter와 다르다는 점이 PR description에 언급되지 않았다. 신규 룰은 daily/weekly profile 모두 영향. Resolved (1), Still open (1).
Co-signed: https://github.com/rebellions-sw/ssw-bundle/pull/3927#discussion_r4541170001 ×1

| # | Severity | File:Line | Rule | 설명 |
|---|---|---|---|---|
| 1 | MAJOR | inv/test_pipeline/test_pipeline.py:847 | [D22] | `_compose_include` 동작이 dryrun과 다름. `docs/conventions/invoke.md §3` 직접 인용 누락. |

— hyejin-bot 🐱✨
```
