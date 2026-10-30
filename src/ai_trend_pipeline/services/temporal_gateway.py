"""Temporal adapters for the chart run and story candidate gateways."""

from collections.abc import Awaitable
from datetime import UTC, datetime

from temporalio.client import (
    Client,
    WorkflowExecution,
    WorkflowExecutionStatus,
    WorkflowHandle,
)
from temporalio.common import (
    WorkflowIDConflictPolicy,
    WorkflowIDReusePolicy,
)
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from ai_trend_pipeline.daily_chart import (
    DAILY_CHART_TASK_QUEUE,
    ApprovalDecision,
    DailyChartInput,
    DailyChartResult,
    DailyChartWorkflow,
    ReviewState,
)
from ai_trend_pipeline.services.candidates import (
    CandidateNotFound,
    CandidatePage,
    CandidateSummary,
    CandidateUnavailable,
    ListCandidatesRequest,
    decode_page_token,
    encode_page_token,
)
from ai_trend_pipeline.services.charts import RunAlreadyExists, RunNotFound
from ai_trend_pipeline.services.stories import (
    CANDIDATE_WORKFLOW_TYPE,
    MAX_SEEN_CANDIDATES,
    MAX_STORY_SCAN_PAGES,
    ListStoriesRequest,
    StoryFilter,
    StoryListPage,
    StoryPageToken,
    StoryStatusUnavailable,
    StoryVisibilityRow,
    build_story_row,
    collapse_story_rows,
    decode_story_page_token,
    encode_story_page_token,
    matches_story_filter,
    retained,
    sort_story_rows,
    story_visibility_query,
)
from ai_trend_pipeline.source_ingestion import (
    SOURCE_INGESTION_TASK_QUEUE,
    SourceIngestionInput,
    SourceIngestionWorkflow,
    source_ingestion_workflow_id,
)
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.story_candidate import (
    CANDIDATE_SOURCE_ATTRIBUTE,
    CANDIDATE_STATUS_ATTRIBUTE,
    CANDIDATE_TASK_QUEUE,
    CandidateDecision,
    CandidateReviewState,
    CandidateStatus,
    StoryCandidate,
    StoryCandidateInput,
    StoryCandidateResult,
    StoryCandidateWorkflow,
    candidate_id_for,
    candidate_memo,
    candidate_search_attributes,
)
from ai_trend_pipeline.story_visibility import (
    STORY_CANDIDATE_ID_ATTRIBUTE,
    STORY_CREATED_AT_ATTRIBUTE,
    STORY_SOURCE_ATTRIBUTE,
    STORY_STATUS_ATTRIBUTE,
    STORY_STATUS_DETAIL_ATTRIBUTE,
    STORY_TITLE_ATTRIBUTE,
    STORY_UPDATED_AT_ATTRIBUTE,
)


def started_since_query(since: datetime) -> str:
    stamp = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"WorkflowType = 'DailyChartWorkflow' AND StartTime >= '{stamp}'"


async def _translate[T](wf_id: str, call: Awaitable[T]) -> T:
    try:
        return await call
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            raise RunNotFound(wf_id) from exc
        raise


class TemporalChartRunHandle:
    def __init__(self, handle: WorkflowHandle[DailyChartWorkflow, DailyChartResult]) -> None:
        self._handle = handle

    @property
    def id(self) -> str:
        return self._handle.id

    async def review_state(self) -> ReviewState:
        return await _translate(self.id, self._handle.query(DailyChartWorkflow.review_state))

    async def send_decision(self, decision: ApprovalDecision) -> None:
        await _translate(self.id, self._handle.signal(DailyChartWorkflow.decide, decision))

    async def result(self) -> DailyChartResult:
        return await _translate(self.id, self._handle.result())


class TemporalChartRunGateway:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def start(self, wf_id: str, data: DailyChartInput) -> None:
        memo = {"requested_by": data.requested_by} if data.requested_by is not None else None
        try:
            await self._client.start_workflow(
                DailyChartWorkflow.run,
                data,
                id=wf_id,
                task_queue=DAILY_CHART_TASK_QUEUE,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
                memo=memo,
            )
        except WorkflowAlreadyStartedError as exc:
            raise RunAlreadyExists(wf_id) from exc

    async def count_started_since(self, requested_by: str, since: datetime) -> int:
        count = 0
        async for wf in self._client.list_workflows(started_since_query(since)):
            if await wf.memo_value("requested_by", None, type_hint=str) == requested_by:
                count += 1
        return count

    def handle(self, wf_id: str) -> TemporalChartRunHandle:
        return TemporalChartRunHandle(
            self._client.get_workflow_handle_for(DailyChartWorkflow.run, wf_id)
        )


class TemporalSourceIngestionGateway:
    """Starts (or returns) the one ingestion workflow per configured source."""

    def __init__(self, client: Client) -> None:
        self._client = client

    async def start(self, source: SourceName, data: SourceIngestionInput) -> str:
        workflow_id = source_ingestion_workflow_id(source)
        try:
            await self._client.start_workflow(
                SourceIngestionWorkflow.run,
                data,
                id=workflow_id,
                task_queue=SOURCE_INGESTION_TASK_QUEUE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            # Defensive: USE_EXISTING normally returns the existing run instead of raising.
            return workflow_id
        return workflow_id


def _query_stamp(moment: datetime) -> str:
    stamp = moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return stamp


def pending_candidates_query(cutoff: datetime) -> str:
    return (
        f"WorkflowType = '{CANDIDATE_WORKFLOW_TYPE}' AND "
        f"CandidateStatus = '{CandidateStatus.AWAITING_REVIEW.value}' AND "
        f"StoryCreatedAt > '{_query_stamp(cutoff)}'"
    )


async def _translate_candidate_not_found[T](candidate_id: str, call: Awaitable[T]) -> T:
    try:
        return await call
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            raise CandidateNotFound(candidate_id) from exc
        raise


class TemporalCandidateHandle:
    def __init__(
        self, handle: WorkflowHandle[StoryCandidateWorkflow, StoryCandidateResult]
    ) -> None:
        self._handle = handle

    @property
    def id(self) -> str:
        return self._handle.id

    async def review_state(self) -> CandidateReviewState:
        return await _translate_candidate_not_found(
            self.id, self._handle.query(StoryCandidateWorkflow.review_state)
        )

    async def approve(self, decision: CandidateDecision) -> None:
        await _translate_candidate_not_found(
            self.id, self._handle.signal(StoryCandidateWorkflow.approve, decision)
        )

    async def reject(self, decision: CandidateDecision) -> None:
        await _translate_candidate_not_found(
            self.id, self._handle.signal(StoryCandidateWorkflow.reject, decision)
        )

    async def result(self) -> StoryCandidateResult:
        return await _translate_candidate_not_found(self.id, self._handle.result())


class TemporalCandidateGateway:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def start(self, candidate: StoryCandidate, review_timeout_seconds: int) -> None:
        candidate_id = candidate_id_for(candidate.source, candidate.external_id)
        try:
            await self._client.start_workflow(
                StoryCandidateWorkflow.run,
                StoryCandidateInput(
                    candidate=candidate, review_timeout_seconds=review_timeout_seconds
                ),
                id=candidate_id,
                task_queue=CANDIDATE_TASK_QUEUE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                search_attributes=candidate_search_attributes(
                    candidate, updated_at=datetime.now(UTC)
                ),
                memo=candidate_memo(candidate),
            )
        except WorkflowAlreadyStartedError:
            # The deterministic id already names a closed run: the existing candidate wins.
            return

    async def list_pending(
        self, request: ListCandidatesRequest, *, cutoff: datetime
    ) -> CandidatePage:
        token = decode_page_token(request.page_token) if request.page_token is not None else None
        try:
            iterator = self._client.list_workflows(
                pending_candidates_query(cutoff),
                page_size=request.limit,
                next_page_token=token,
            )
            await iterator.fetch_next_page()
            rows = iterator.current_page or []
            summaries: list[CandidateSummary] = []
            for workflow_execution in rows:
                summary = await _candidate_summary(workflow_execution)
                if summary is not None:
                    summaries.append(summary)
            next_token = (
                encode_page_token(iterator.next_page_token)
                if iterator.next_page_token is not None
                else None
            )
        except RPCError as exc:
            raise CandidateUnavailable() from exc
        return CandidatePage(candidates=summaries, next_token=next_token)

    def handle(self, candidate_id: str) -> TemporalCandidateHandle:
        return TemporalCandidateHandle(
            self._client.get_workflow_handle_for(StoryCandidateWorkflow.run, candidate_id)
        )


async def _candidate_summary(workflow_execution: WorkflowExecution) -> CandidateSummary | None:
    attributes = workflow_execution.typed_search_attributes
    source_value = attributes.get(CANDIDATE_SOURCE_ATTRIBUTE)
    status_value = attributes.get(CANDIDATE_STATUS_ATTRIBUTE)
    if not isinstance(source_value, str) or not isinstance(status_value, str):
        return None
    try:
        source = SourceName(source_value)
        status = CandidateStatus(status_value)
    except ValueError:
        return None
    memo = await workflow_execution.memo()
    title = memo.get("candidate_title")
    proposed_angle = memo.get("candidate_proposed_angle")
    observed_raw = memo.get("candidate_observed_at")
    observed_at: datetime | None = None
    if isinstance(observed_raw, str):
        try:
            observed_at = datetime.fromisoformat(observed_raw)
        except ValueError:
            observed_at = None
    return CandidateSummary(
        candidate_id=workflow_execution.id,
        source=source,
        title=title if isinstance(title, str) else "",
        proposed_angle=proposed_angle if isinstance(proposed_angle, str) else "",
        observed_at=observed_at,
        status=status,
    )


def _datetime_value(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return None


def _next_story_token(
    filter: StoryFilter,
    raw: bytes | None,
    seen: list[str],
    emitted: list[StoryVisibilityRow],
) -> str | None:
    combined = list(seen)
    combined.extend(row.candidate_id for row in emitted if row.candidate_id is not None)
    deduped = list(dict.fromkeys(combined))
    if len(deduped) > MAX_SEEN_CANDIDATES:
        return None
    raw_token = encode_page_token(raw) if raw is not None else None
    return encode_story_page_token(StoryPageToken(filter=filter, raw=raw_token, seen=deduped))


class TemporalStoryStatusGateway:
    """Reads both workflow types through one visibility query; no per-row workflow call."""

    def __init__(self, client: Client) -> None:
        self._client = client

    async def _visibility_row(self, execution: WorkflowExecution) -> StoryVisibilityRow:
        attributes = execution.typed_search_attributes
        title_value = attributes.get(STORY_TITLE_ATTRIBUTE)
        source_value = attributes.get(STORY_SOURCE_ATTRIBUTE)
        status_value = attributes.get(STORY_STATUS_ATTRIBUTE)
        candidate_value = attributes.get(STORY_CANDIDATE_ID_ATTRIBUTE)
        created_value = attributes.get(STORY_CREATED_AT_ATTRIBUTE)
        updated_value = attributes.get(STORY_UPDATED_AT_ATTRIBUTE)
        detail_value = attributes.get(STORY_STATUS_DETAIL_ATTRIBUTE)

        title = title_value if isinstance(title_value, str) else ""
        source = source_value if isinstance(source_value, str) else ""
        status = status_value if isinstance(status_value, str) else ""
        candidate_id = candidate_value if isinstance(candidate_value, str) else None
        created_at = _datetime_value(created_value)
        updated_at = _datetime_value(updated_value)
        detail = detail_value if isinstance(detail_value, str) else None

        # Pre-053 candidate rows carry only the candidate keys; read memos only when the
        # shared attribute is absent.
        if not source:
            source_fallback = attributes.get(CANDIDATE_SOURCE_ATTRIBUTE)
            if isinstance(source_fallback, str):
                source = source_fallback
        if not status:
            status_fallback = attributes.get(CANDIDATE_STATUS_ATTRIBUTE)
            if isinstance(status_fallback, str):
                status = status_fallback
        if not title or created_at is None:
            memo = await execution.memo()
            if not title:
                memo_title = memo.get("candidate_title")
                if isinstance(memo_title, str):
                    title = memo_title
            if created_at is None:
                created_at = _datetime_value(memo.get("candidate_observed_at"))
        if candidate_id is None and execution.workflow_type == CANDIDATE_WORKFLOW_TYPE:
            candidate_id = execution.id

        execution_closed = (
            execution.status is not None and execution.status is not WorkflowExecutionStatus.RUNNING
        )
        return StoryVisibilityRow(
            workflow_type=execution.workflow_type,
            workflow_id=execution.id,
            candidate_id=candidate_id,
            title=title,
            source=source,
            internal_status=status,
            status_detail=detail,
            created_at=created_at,
            updated_at=updated_at,
            execution_closed=execution_closed,
        )

    async def list_stories(
        self, request: ListStoriesRequest, *, now: datetime, cutoff: datetime
    ) -> StoryListPage:
        token = (
            decode_story_page_token(request.page_token, filter=request.filter)
            if request.page_token is not None
            else StoryPageToken(filter=request.filter)
        )
        query = story_visibility_query(request.filter, cutoff)
        seen = list(token.seen)
        seen_ids = set(seen)
        cursor = decode_page_token(token.raw) if token.raw is not None else None
        # An overflow re-scans from where this request started, not from the page that
        # produced it: rows from earlier pages can sort below rows on the current page.
        scan_start = cursor
        accumulated: list[StoryVisibilityRow] = []
        try:
            pages_scanned = 0
            while pages_scanned < MAX_STORY_SCAN_PAGES:
                pages_scanned += 1
                iterator = self._client.list_workflows(
                    query, page_size=request.limit, next_page_token=cursor
                )
                await iterator.fetch_next_page()
                for execution in iterator.current_page or []:
                    row = await self._visibility_row(execution)
                    if row.candidate_id is None or row.created_at is None:
                        continue
                    if row.candidate_id in seen_ids:
                        continue
                    if not retained(row.created_at, cutoff):
                        continue
                    accumulated.append(row)
                collapsed = collapse_story_rows(accumulated, now=now)
                matching = sort_story_rows(
                    [row for row in collapsed if matches_story_filter(row, request.filter)]
                )
                cursor = iterator.next_page_token
                if len(matching) > request.limit:
                    emitted = matching[: request.limit]
                    return StoryListPage(
                        stories=[build_story_row(row) for row in emitted],
                        next_token=_next_story_token(request.filter, scan_start, seen, emitted),
                    )
                if cursor is None:
                    return StoryListPage(stories=[build_story_row(row) for row in matching])
                if pages_scanned >= MAX_STORY_SCAN_PAGES:
                    return StoryListPage(
                        stories=[build_story_row(row) for row in matching],
                        next_token=_next_story_token(request.filter, cursor, seen, matching),
                    )
        except RPCError as exc:
            raise StoryStatusUnavailable() from exc
        return StoryListPage()
