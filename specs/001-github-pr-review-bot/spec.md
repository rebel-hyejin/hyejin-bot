# Feature Specification: GitHub PR Review Automation Bot

**Feature Branch**: `001-github-pr-review-bot`
**Created**: 2026-05-04
**Status**: Draft
**Input**: User description: "Github PR 리뷰 자동화 봇 — 나에게 요청된 PR 리뷰가 있으면 자동으로 코드 리뷰를 달아준다. 리뷰는 운영자만의 code review skill 또는 hyejin-bot의 코드 리뷰 지향점을 담은 페르소나에 기반하며, 출력에는 Summary가 반드시 포함되고 지적·보완 사항은 inline comment로 단다."

## Clarifications

### Session 2026-05-04

- Q: GitHub 인증 방식 → A: 운영자 머신의 `gh` CLI 로컬 인증 상태에 위임 (예: `gh auth token`으로 토큰 추출). 봇은 별도 PAT/Keychain 엔트리를 만들지 않으며, `gh`의 자격증명 저장소가 단일 진실 원천. 토큰 갱신은 운영자가 `gh auth refresh`로 처리.
- Q: 리뷰 게시 메커니즘 → A: GitHub Pull Request Review API (`POST /repos/{owner}/{repo}/pulls/{n}/reviews`)를 사용해 Summary(review body) + inline 지적(review comments[])을 단일 review 객체로 atomic 게시. `event="COMMENT"`로 보내 approve/request-changes 권한은 사용하지 않음. PR의 title/body/labels/milestone/assignees/base branch/state는 절대 수정하지 않음.
- Q: "리뷰 가능한 크기" 임계값 → A: 변경 라인 수(추가+삭제) **1000줄 초과** OR 변경 파일 수 **50개 초과** 중 먼저 도달하는 쪽. 두 임계값 모두 config knob으로 운영자가 페르소나 튜닝과 함께 조정 가능 (기본값 1000/50).
- Q: 페르소나 문서 위치/포맷/변종 선택 → A: `~/.claude/skills/<name>/SKILL.md` 경로의 Claude Code skill 포맷 (frontmatter + body). 봇은 frontmatter를 무시하고 body만 system prompt로 로드 — 즉 IDE에서 같은 skill을 수동 호출해도 동일하게 작동함. 여러 변종은 `~/.claude/skills/` 아래 다중 디렉토리로 구성하고, `config.toml`의 `[handlers.pr_review].persona_skill = "<name>"`로 활성 변종을 선택. Hot-edit은 매 리뷰마다 SKILL.md의 mtime을 stat()해 변경 감지 시 다시 읽음.
- Q: force re-review의 "supersede" 동작 → A: chronological supersede + 명시적 표기. 새 review를 그대로 추가 게시하되, Summary 첫 줄에 "Updated review for SHA `<sha>` (supersedes earlier bot review posted at `<HH:MM:SS UTC>`)"를 의무 포함. 이전 봇 review와 inline 코멘트는 GitHub API 제약상 삭제 불가능하므로 history에 보존됨. dedup 레코드는 force 시 "최신 review id"를 갱신하지만 이전 review id 이력도 함께 보관.
- Q: re-request 시점 의미 (리뷰 트리거 단위) → A: 트리거 단위는 SHA가 아니라 **"리뷰 요청 인스턴스"**. (a) 새로 reviewer 지정 (b) 새 commit push (c) author가 "Re-request review" 클릭 — 이 셋 모두를 별도 이벤트로 취급. 폴링 트리거가 `gh_review_requested_state` 테이블로 PR마다 `(head_sha, request_gen, in_pending_set)`를 추적하다가, "PR이 pending set에서 사라졌다가 다시 등장" 또는 "head_sha 변경"을 관측하면 `request_gen`을 증가시켜 새 이벤트 발행. dedup_token = `sha256("gh-review-requested|{repo}#{pr}@{head_sha}#{gen}")`. 같은 (head, gen) 폴링 중복 관측은 emit 안 함.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Manual review of a specific PR using the persona (Priority: P1)

The operator points the bot at a single GitHub pull request (by URL or `owner/repo#number`) and the bot produces a review based on the operator's review persona. The posted review always includes a top-level **Summary**, and any concrete issues or improvement suggestions are posted as **inline comments** anchored to specific files and lines in the diff.

**Why this priority**: Without this slice nothing else is meaningful. It validates the full output pipeline (PR fetch → persona-driven review generation → summary + inline comments posted to GitHub) without depending on GitHub event delivery, so it ships first and de-risks P2.

**Independent Test**: Operator authors a minimal review persona, runs a "review this PR" command pointing at a real PR they own (one that contains at least one obvious issue and one clean section), and confirms the resulting review on GitHub has both a Summary and at least one inline comment on the issue.

**Acceptance Scenarios**:

1. **Given** the bot is running and the review persona document exists, **When** the operator triggers a manual review for `owner/repo#123`, **Then** within 5 minutes a review appears on the PR containing a Summary section and (if issues exist) inline comments anchored to specific files and lines.
2. **Given** the same PR has already been reviewed at the current head commit AND no new review request has arrived since, **When** the operator triggers another review without specifying "force", **Then** the bot reports "already reviewed at this commit (request_gen=N)" and posts nothing new to GitHub.
3. **Given** the PR URL is malformed or refers to a repo the operator cannot access, **When** the operator triggers a review, **Then** the bot returns a clear error and posts nothing to GitHub.
4. **Given** the PR contains code changes but the persona's checks find nothing to flag, **When** the bot reviews it, **Then** the bot posts a Summary acknowledging the review and posts zero inline comments (positive case must not invent issues).
5. **Given** the operator passes the "force" flag for an already-reviewed head commit, **When** the bot reviews it, **Then** a new review is posted whose Summary first line explicitly identifies it as "supersedes earlier bot review posted at HH:MM:SS UTC"; the prior review remains in PR history (GitHub API does not allow deleting it).

---

### User Story 2 - Auto review when the operator is requested as a reviewer (Priority: P2)

When someone on GitHub adds the operator as a reviewer on a pull request (the GitHub "Request review" action), the bot detects this and automatically produces a review on the operator's behalf using the same persona-driven pipeline as P1. This is the headline feature — the operator's reviewer queue gets first-pass coverage automatically.

**Why this priority**: This is the actual problem statement ("나에게 요청된 PR 리뷰가 있으면 자동으로 코드 리뷰 달아주는 것"). It depends on P1's output pipeline working, so it lands second. The trigger condition itself is narrow ("operator was requested"), which removes the need for a per-repo allow-list.

**Independent Test**: A collaborator adds the operator as a reviewer on a PR (in any repo where the operator has access). Within 10 minutes, a review appears on that PR — Summary plus inline comments on flagged lines — without any operator command.

**Acceptance Scenarios**:

1. **Given** the bot is running and the operator's GitHub identity is known, **When** the operator is added as a requested reviewer on a PR, **Then** within 10 minutes a review (Summary + inline comments where applicable) is posted on that PR.
2. **Given** the operator was previously requested-and-reviewed at commit A and is then re-requested at commit B (after new pushes), **When** the bot processes the re-request, **Then** the bot posts a fresh review keyed to commit B; the prior review at commit A is left alone.
3. **Given** the operator already reviewed PR `owner/repo#42` at head commit A AND the PR author then clicks "Re-request review" (head SHA still A, no new pushes), **When** the polling trigger observes the PR re-entering the review-requested set, **Then** the bot increments `request_gen` for that PR and emits a new event; a fresh review is posted at the same head A and is marked as superseding the prior bot review.
4. **Given** the operator was requested but is then removed as a reviewer before the bot starts processing, **When** the bot reaches that request, **Then** the bot skips it and records "request withdrawn" without posting.
5. **Given** the bot is requested-as-reviewer on a PR opened by the operator themselves, **When** the bot processes the event, **Then** the bot skips the self-review and records "self-authored, skipped".
6. **Given** the same review-requested event is observed by the polling trigger twice (overlapping polls or restart-replay), **When** both observations reach the dispatcher, **Then** at most one review is posted for that `(repo, PR, head SHA, request_gen)` tuple.

---

### User Story 3 - Persona governs review style and is hot-editable (Priority: P2)

The bot's review behavior is driven by an operator-authored persona document (the operator's "code review skill"). The operator can edit the persona at any time; the next review uses the updated persona without restarting the bot.

**Why this priority**: P2 because the persona is what makes this bot specifically the operator's reviewer rather than a generic one. The user explicitly framed the feature around it. Bumping pause/kill-switch to P3 reflects that real value comes from persona quality, not from kill-switches.

**Independent Test**: Operator runs a manual review on a PR and inspects the output. Then operator edits the persona (e.g., adds "always flag missing tests"), triggers a fresh review on a different PR, and verifies the new persona's instruction is reflected in the output.

**Acceptance Scenarios**:

1. **Given** a review persona exists and the bot has reviewed at least one PR with it, **When** the operator edits the persona document and saves, **Then** the next review the bot generates reflects the edited content (no daemon restart required).
2. **Given** the persona document is missing, unreadable, or fails operator-defined sanity checks, **When** a review request is processed, **Then** the bot does not post a review; the request is sent to the dead-letter list with a clear "persona unavailable" reason.
3. **Given** multiple persona variants exist as separate `~/.claude/skills/<name>/` directories, **When** the operator changes `[handlers.pr_review].persona_skill` in `config.toml` and reloads config, **Then** the bot uses the selected variant's SKILL.md for subsequent reviews.

---

### User Story 4 - Operator pause kill-switch (Priority: P3)

The operator can pause the bot globally so no review comments are posted to GitHub, and unpause it later without losing pending review requests.

**Why this priority**: Operational safety net rather than core value. Manual workarounds exist (revoke token, kill process), but a clean pause is needed before relying on the bot daily. Lower than persona/auto-trigger because nothing in the user's request hinges on it.

**Independent Test**: With the bot running and processing review-requested events, the operator issues a pause; subsequent review-requested events arrive but no GitHub comments are posted. After unpause, the queued reviews complete.

**Acceptance Scenarios**:

1. **Given** the bot is running and auto-reviewing PRs, **When** the operator issues a global pause, **Then** new review requests are accepted and queued, but no review comments are posted to GitHub until unpause.
2. **Given** the bot is paused with several queued requests, **When** the operator unpauses, **Then** each queued request is processed once (no duplicates, no losses) and the first review posts within 5 minutes of unpause.
3. **Given** the bot is in the middle of generating a review when pause is issued, **When** generation finishes, **Then** the resulting review is held in the queue and not posted until unpause.

---

### Edge Cases

- **Empty or non-code PR**: PR contains only image / lock-file / generated-artifact changes. Bot posts a brief Summary noting "no source changes to review" and zero inline comments — does not invent issues.
- **Very large PR**: Diff exceeds the bot's reviewable size budget. Bot posts a single Summary explaining the PR is too large to review automatically (recommending a split) and zero inline comments — never a partial / truncated review.
- **Force-push during review**: Head commit changes between diff fetch and review post. The posted Summary explicitly identifies which commit SHA was reviewed, and inline comments whose anchor lines no longer exist in the new diff fall back into the Summary section as bullet points (the review is still posted, never silently dropped).
- **Already-reviewed commit (no new request)**: Re-triggering a review on the same `(repo, PR, head SHA, request_gen)` tuple — i.e. nothing has changed on GitHub's side since the last bot review — is a no-op unless the operator explicitly forces a re-review.
- **Re-request at same head SHA**: Author clicks "Re-request review" without pushing new commits, so head SHA is unchanged. The polling trigger detects the PR re-entering its `review-requested:@me` result set, increments `request_gen` for that PR, and emits a fresh event. A new bot review is posted at the same head SHA and its Summary first line marks it as superseding the prior bot review. (This is what makes "request instance" — not just head SHA — the correct trigger unit.)
- **Force re-review**: A new review is posted; its Summary first line marks it as "supersedes earlier bot review posted at HH:MM:SS UTC". The previous review remains visible in the PR history (GitHub does not allow deleting/dismissing `event=COMMENT` reviews via API). Operator-facing UX is unambiguous about which review is the latest.
- **Review request withdrawn**: Operator is removed as a reviewer (or all reviewers are dismissed) before the bot starts processing. Bot skips and records "request withdrawn"; no review is posted.
- **Self-authored PR**: Operator is requested as a reviewer on their own PR (e.g., automation requesting all repo members). Bot skips and records "self-authored".
- **Persona missing or invalid**: Configured persona document doesn't exist, can't be parsed, or fails the operator-defined sanity check. Bot does not post a generic fallback review; the request goes to dead-letter with a clear reason.
- **PR closed mid-review**: PR is closed/merged while the bot is generating its review. Bot posts the review anyway (the work is done; Summary identifies the SHA reviewed).
- **GitHub or AI service outage**: Network failure or rate-limit response from either side. Request is retried with backoff; if retries exhaust, request goes to dead-letter for operator inspection and replay.
- **Sensitive content in diff**: PR diff contains content that looks like a secret (API key, password). Bot must not echo such content verbatim in any Summary or inline comment.
- **Self-loop on bot's own comments**: The bot's previously-posted review comments must never trigger a new review pass.
- **No issues to flag**: When the persona finds nothing actionable, the bot still posts a Summary (so the requester sees that the review happened) but posts zero inline comments.

## Requirements *(mandatory)*

### Functional Requirements

#### Triggers

- **FR-001**: The bot MUST allow the operator to trigger a review of a specified GitHub PR (URL or `owner/repo#number`) on demand.
- **FR-002**: The bot MUST automatically trigger a review whenever the operator is added as a requested reviewer on a GitHub pull request — regardless of which repository the PR lives in (within the operator's GitHub access scope).
- **FR-003**: The bot MUST NOT auto-trigger reviews on any other condition (PR opened, label added, comment posted, etc.). Manual trigger remains the override for those cases.
- **FR-004**: The bot MUST skip review-requests that name a PR authored by the operator themselves and MUST skip requests that have been withdrawn before the bot begins processing.

#### Review generation

- **FR-005**: The bot MUST generate every review using an operator-authored review persona stored as a Claude Code skill at `~/.claude/skills/<name>/SKILL.md`. Active persona name is set in `config.toml` under `[handlers.pr_review].persona_skill`. The bot MUST load only the SKILL.md body (the markdown content after the optional YAML frontmatter) as the review system prompt; frontmatter MUST be parsed-but-ignored at runtime.
- **FR-006**: Edits to the active persona's SKILL.md MUST take effect on the next review without requiring a daemon restart. The bot MUST detect changes by comparing the file's modification time against the last-loaded value (stat-on-each-review is acceptable; a long-lived in-memory cache that ignores mtime is not).
- **FR-007**: If the active persona's SKILL.md is missing, unreadable, empty after frontmatter stripping, or fails sanity validation (e.g., body is shorter than a minimum operator-defined length), the bot MUST NOT post a generic fallback review; the request MUST be sent to the dead-letter list with a "persona unavailable: \<reason>" message.
- **FR-007a**: Switching the active persona MUST be possible by editing `config.toml` and triggering a config reload (or restart) — switching MUST NOT require code changes.
- **FR-008**: Before generating a review, the bot MUST fetch at minimum: PR title, PR body, head commit SHA, list of changed files, and the unified diff for those files.

#### Review output structure

- **FR-009**: Every posted review MUST include a top-level Summary section that names the head commit SHA reviewed and gives a written overview of the changes and the bot's overall conclusion. The Summary MUST be posted as the body of a GitHub Pull Request **Review** object (not as a freestanding issue comment).
- **FR-010**: When the bot has concrete issues, suggestions, or improvement points, those MUST be posted as inline comments anchored to specific files and line ranges in the diff — NOT as bullet points in the Summary. Inline comments MUST be submitted as part of the same review object that carries the Summary, so they appear together as one review in GitHub's UI.
- **FR-010a**: The review MUST be submitted with `event="COMMENT"`. The bot MUST NOT submit reviews with `event="APPROVE"` or `event="REQUEST_CHANGES"`.
- **FR-010b**: The bot MUST NOT modify the PR itself — title, body/description, labels, milestones, assignees, base branch, or open/closed state are all read-only from the bot's perspective. The bot only adds review objects to the PR.
- **FR-011**: When the bot has nothing to flag (clean review), the Summary MUST still be posted, with zero inline comments, so the requester can see the review completed.
- **FR-012**: When a target line of an inline comment no longer exists in the latest diff (force-push race), the bot MUST fall back to including that feedback as a bullet in the Summary; it MUST NOT fail the entire review.
- **FR-013**: When a PR's diff exceeds the reviewable size budget — defined as **more than 1000 changed lines (additions + deletions in the unified diff)** OR **more than 50 changed files**, whichever is reached first — the bot MUST post a single "PR too large for automated review" Summary and zero inline comments. Never produce a partial/truncated review. Both thresholds MUST be operator-configurable (config knob), with `1000` and `50` as the defaults.

#### Identity & access

- **FR-014**: The bot MUST authenticate to GitHub using credentials sourced from the operator's local `gh` CLI authentication state (e.g., via `gh auth token`). The bot MUST NOT maintain its own GitHub PAT in Keychain or in a separate secrets file, MUST NOT register a GitHub App installation, and MUST NOT run its own OAuth device-flow. If `gh` CLI is not authenticated, the bot MUST surface a clear error and refuse to start the GitHub triggers/handlers.
- **FR-015**: The bot MUST NOT include any of the operator's secrets, credentials, persona file paths, or local configuration paths in any posted Summary or inline comment.

#### Reliability & operability

- **FR-016**: The bot MUST record `(repo, PR number, head commit SHA, request_gen, review status)` for every review attempt, and MUST refuse to post a duplicate review for the same `(repo, PR, head SHA, request_gen)` tuple unless the operator explicitly forces a re-review.
- **FR-017**: A force re-review at the same head commit MUST be posted as a new GitHub review (the API does not permit deleting or dismissing the bot's prior `event=COMMENT` review). The new review's Summary first line MUST explicitly mark it as superseding the prior review, in the form: `Updated review for SHA <sha> (supersedes earlier bot review posted at <HH:MM:SS UTC>)`. The bot's dedup record for `(repo, PR#, head SHA, request_gen)` MUST be updated to point at the new review's identifiers; the prior review's identifiers MUST be retained in history (not overwritten) for audit.
- **FR-018**: The polling trigger MUST track each `(repo, PR)` it has observed in its review-requested result set and MUST emit a new event whenever any of the following occur: (a) the PR enters the result set for the first time; (b) the PR's head SHA changes while still in the set; (c) the PR leaves the set and later re-enters it (author re-requested review). Each distinct trigger event MUST carry an incrementing `request_gen` counter so that the dedup token derived from `(repo, PR, head SHA, request_gen)` is unique per request instance, while redundant polling observations of the same `(head SHA, request_gen)` MUST NOT emit a new event.
- **FR-018a**: The dedup token written to `events.dedup_token` for an auto-trigger event MUST be deterministic — `sha256("gh-review-requested|{repo}#{pr}@{head_sha}#{gen}")` — so that an `INSERT ... ON CONFLICT(dedup_token) DO NOTHING` against `events` is the only race-safe path the trigger uses to enqueue work.
- **FR-019**: The bot MUST treat each review request as recoverable: if the bot crashes or restarts mid-review, the request is either re-attempted on next boot or moved to dead-letter — never silently dropped.
- **FR-020**: The bot MUST tolerate GitHub and AI-provider rate limits and transient network errors with retry-with-backoff; non-retryable errors MUST send the request to dead-letter.
- **FR-021**: The bot MUST allow the operator to pause and unpause review posting globally without losing queued review requests.
- **FR-022**: The operator MUST be able to inspect, for each review request, its current state (queued / in-progress / posted / skipped / dead-letter) along with the PR identifier and head commit SHA.

### Key Entities *(include if feature involves data)*

- **Review Request**: A unit of work asking the bot to review one PR at one head commit, scoped to one request instance. Attributes: requesting source (manual / auto-via-review-requested), repository, PR number, head commit SHA, `request_gen` (auto-trigger: monotonic integer counter per `(repo, PR)`; manual non-force trigger: sentinel `"0"`; manual force trigger: sentinel `"manual_<unix_ts>"` so each force run is a distinct request instance), requested-at timestamp, status, attempt count, last error.
- **GitHub Review-Request Tracking State**: Per-PR state the polling trigger maintains so it can recognize re-requests at the same head SHA. Stored as one row per `(repo, PR number)` with attributes: current `head_sha`, current `request_gen` (incremented every time the PR re-enters the review-requested result set or its head SHA changes), `in_pending_set` flag (whether the most recent poll saw this PR in `review-requested:@me`), `last_observed_at` timestamp. The trigger updates this table inside the same SQLite transaction that writes `events`/`outbox` rows, so observation and emission are atomic.
- **Review Persona**: The operator-authored Claude Code skill describing review focus, tone, and priorities. Source location: `~/.claude/skills/<name>/SKILL.md`. Attributes: skill directory name (= variant identifier), SKILL.md body content (markdown after optional YAML frontmatter; frontmatter parsed-but-ignored), last-modified timestamp (used for hot-reload detection).
- **Review Result**: The output the bot produced for a Review Request. Attributes: Summary text, ordered list of inline comments (each with file path, line range, body), generated-at timestamp, the commit SHA reviewed, the GitHub identifiers returned after posting.
- **Pause State**: Global flag indicating whether the bot is currently allowed to post reviews. Attributes: paused (yes/no), reason note, set-by, set-at.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For PRs under 50 changed files, an operator-triggered review (P1) results in a posted review (Summary + zero or more inline comments) within 5 minutes in 95% of attempts.
- **SC-002**: When the operator is added as a requested reviewer on a PR (P2), the bot posts its review within 10 minutes in 95% of cases.
- **SC-003**: 100% of posted reviews contain a Summary section identifying the head commit SHA. 0% of reviews are posted without a Summary.
- **SC-004**: When the persona finds at least one issue worth flagging, ≥90% of those issues appear as inline comments (anchored to file/line) rather than as bullets in the Summary, in operator-sampled review audits.
- **SC-005**: After the operator edits the persona document and saves, the very next review (without a daemon restart) reflects the edit, verified by an operator-defined regression PR (qualitative — measured by the operator on each persona iteration).
- **SC-006**: The bot posts at most one review per `(repo, PR number, head commit SHA, request_gen)` tuple in 100% of cases (zero duplicates from a single request instance), regardless of overlapping polls, daemon restarts, or replay. Distinct request instances at the same head SHA (e.g., author clicks "Re-request review") DO produce a new review; that is the intended behavior, not a duplicate.
- **SC-007**: While paused, the bot posts zero review comments to GitHub. After unpause, every queued request is processed (none dropped) and the first one posts within 5 minutes.
- **SC-008**: The bot never posts content matching the operator's known-bad secret fixture set in any Summary or inline comment — 100% pass rate on automated redaction checks.
- **SC-009**: For PRs that exceed the configured size budget (default: >1000 changed lines OR >50 changed files), the bot posts the "too large to review" Summary in 100% of such cases instead of producing a truncated review.
- **SC-010**: When auditing 20 randomly chosen real reviews posted by the bot, ≥80% are judged by the operator to contain at least one piece of actionable, persona-aligned feedback (qualitative quality bar).
- **SC-011**: The bot reviews 100% of PRs where the operator was added as a requested reviewer (subject to the dead-letter exceptions: persona unavailable, withdrawn, self-authored, etc.) and 0% of PRs where the operator was not requested.

## Assumptions

- **Single operator, single tenant**: The bot runs under one operator's GitHub identity and posts reviews as that identity. There is no separate "bot account"; review comments are attributed to the operator. Revisit only if the operator later requests a dedicated bot identity.
- **GitHub auth via `gh` CLI**: The bot does not store GitHub credentials of its own. It delegates to the operator's local `gh` CLI authentication (`gh auth login`); the bot reads the active token via `gh auth token` at boot/refresh time. Token rotation is the operator's responsibility (via `gh auth refresh`). This means GitHub-rate-limit headroom is the standard `gh` user budget (~5000 req/hr REST), and the bot does not need a new entry in the Phase 4 Keychain/0600 secrets stack for GitHub.
- **Trigger scope**: The auto-trigger fires only on "operator added as a requested reviewer on a PR" (GitHub's `review_requested` semantics, applied to the operator personally). Team-level review requests where the operator is implicitly included via a team are out of scope for v1 unless the operator explicitly opts in.
- **No allow-list**: Because the auto-trigger is already narrow ("I was specifically asked to review"), no per-repository allow-list is needed in v1. If the operator later wants to suppress auto-reviews for specific repos, that's a follow-up (deny-list) rather than a blocker.
- **Draft PRs**: If someone explicitly requests the operator's review on a draft PR, the bot reviews it (the requester wanted feedback). Manual triggers also accept drafts.
- **Persona format**: The persona is a Claude Code skill at `~/.claude/skills/<name>/SKILL.md` (decided in Clarifications). The operator can use the same skill interactively in their IDE. The bot ignores the YAML frontmatter and uses only the markdown body as the review system prompt. Multiple variants live as sibling directories under `~/.claude/skills/`; `[handlers.pr_review].persona_skill` in `config.toml` selects which one is active.
- **Review focus**: General code quality, correctness, and improvement suggestions, as defined by the persona. Specialized reviews (security audit, performance profiling, license compliance) are out of scope unless the operator's persona explicitly directs them.
- **Review language**: Generated review prose may be in Korean or English; the operator's preference (Korean) is encoded in the persona, not in code. Not a hard system requirement.
- **Approval authority**: The bot only leaves review comments. It does NOT click "Approve" or "Request changes" in the GitHub-approval sense, does not block merges, and does not change PR state.
- **Daemon integration**: This feature is implemented as one (or more) handlers and one (or more) triggers on top of the existing daemon's outbox / dispatcher / at-least-once delivery contract. The daemon's existing recovery, dead-letter, pause, lifecycle, and redaction semantics apply (see `CLAUDE.md`, `docs/PLAN.md`, `CONTRACTS.md`).
- **Out of scope for v1**: Reviewing GitHub Issues (only PRs); replying to inline-comment threads / multi-turn dialogue; approving or blocking PRs; per-language review templates; SLA dashboards or external metrics export; team-membership-based auto-trigger; allow/deny lists.
