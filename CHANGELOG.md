# Changelog

All notable changes to the Smart Appointment AI Agent project are documented here.

---

## [Unreleased] — 2026-06-12 / 2026-06-15

### Bug Fixes

#### 1. SQLite `database is locked` Error on Concurrent Writes
**Root Cause:** Multiple concurrent HTTP requests (LLM API calls + database writes) competed for SQLite's single write lock without coordination. The original `session_scope()` context manager had no locking mechanism, and the `add_schedule` retry loop was placed *outside* the session scope, so retries never retried the actual database flush.

**Files Changed:**
- `db/base/session_manager.py` — Added process-level `threading.RLock()` write lock and `exclusive=True` parameter to `session_scope()`. All write operations now acquire the lock before opening a DB connection, preventing concurrent writers from racing.
- `db/repositories/technician_repository.py` — `add_technician`, `delete_technician`, `add_schedule`, `update_schedule_status`, `delete_schedule`, `update_technician` now use `exclusive=True`. Read operations (`get_*`, `is_technician_available`) remain lock-free for read performance.
- `db/repositories/knowledge_repository.py` — `add_document`, `update_document`, `delete_document` now use `exclusive=True`.
- `db/repositories/user_behavior_repository.py` — `record_behavior`, `update_user_preference`, `create_recommendation`, `mark_recommendation_sent` now use `exclusive=True`.

#### 2. `MemoryRepository` Holding Detached Session (Hidden Bug)
**Root Cause:** `MemoryRepository.__init__` stored a raw SQLAlchemy session passed from `chat_handler.py`'s `with db.session_scope() as sess:` block. After the `with` block exited, the session was closed, but `MemoryRepository` (and its consumers `ConversationMemoryService`, `SemanticMemoryService`) continued to use it. All subsequent DB operations silently failed or operated on a stale connection. Additionally, `SemanticMemoryService.boost_confidence()` directly called `self.repo.db.flush()` which assumed the detached session was still alive.

**Files Changed:**
- `db/repositories/memory_repository.py` — Complete rewrite: now accepts `SessionManager` instead of a raw session. Every method opens its own `session_scope()` (with lock for writes, without for reads). No session escapes the scope boundary.
- `api/chat_handler.py` — Now passes `SessionManager` (`db`) to `MemoryRepository(db)` instead of a raw session, removing the orphaned `with db.session_scope() as sess:` wrapper.
- `services/semantic_memory_service.py` — `boost_confidence()` no longer calls `self.repo.db.flush()`. Added `MemoryRepository.boost_semantic_memory_confidence()` as a dedicated repository method that opens its own session scope.
- `db/repositories/memory_repository.py` (new method) — Added `boost_semantic_memory_confidence(session_id, key, memory_type, delta)` to encapsulate the confidence-boost update in a proper session scope.

#### 3. Frontend API Port Mismatch (`user_behavior_analysis.html`)
**Root Cause:** `user_behavior_analysis.html` hardcoded `http://127.0.0.1:8000/api/user-behavior/analysis` and `http://127.0.0.1:8000/api/user-behavior/send-reminder`, but the FastAPI server runs on port **8001**. The fetch requests went to a non-existent server, causing all user behavior stats to show "加载失败".

**Files Changed:**
- `web/templates/user_behavior_analysis.html` — Updated both API URLs from port `8000` to `8001`.

#### 4. Appointment Race Condition — Double-Booking (P0)
**Root Cause:** `is_technician_available()` (check) and `add_schedule()` (insert) were two separate DB operations. Under concurrent requests, both could pass the availability check before either inserted, resulting in two overlapping appointments for the same time slot.

**Fix:** Introduced `reserve_slot()` — an atomic check-and-insert method inside a single transaction scope, protected by the existing `_write_lock`. The `AppointmentService.save_appointment()` now calls `reserve_slot()` instead of `add_schedule()`. If the slot is already taken, `SlotTakenException` is raised and `save_appointment()` returns `False`, which triggers the agent to notify the user.

**Files Changed:**
- `db/base/exceptions.py` (new) — `SlotTakenException` custom exception class
- `db/base/interfaces.py` — Added `reserve_slot()` abstract method to `BaseScheduleRepository`
- `db/repositories/technician_repository.py` — Added `reserve_slot()` with inline conflict detection inside `exclusive=True` session scope; simplified `add_schedule()` (removed ineffective retry loop)
- `db/db_router.py` — Added `reserve_slot()` to `TechnicianDBRouter`
- `services/appointment_service.py` — `save_appointment()` now calls `reserve_slot()` instead of `add_schedule()`; catches `SlotTakenException`; removed `time.time()*1000` appointment ID generation (DB auto-generates)
- `db/base/__init__.py` — Exports `SlotTakenException`
- `db/__init__.py` — Imports `SlotTakenException`

#### 5. Missing Admin Templates Causing 500 Errors (P0)
**Root Cause:** `routes.py` registered `/admin` and `/admin/database` endpoints, but the corresponding templates `admin_dashboard.html` and `database_admin.html` did not exist. Accessing either route caused a template-not-found crash.

**Files Changed:**
- `web/templates/admin_dashboard.html` (new) — System dashboard showing knowledge/technician counts, first 5 technicians, category tags, and quick-navigation links
- `web/templates/database_admin.html` (new) — Database management page showing table stats, system info, and navigation

#### 6. Knowledge API Contract Mismatches (P0)
**Root Cause:** Multiple mismatches between `api/knowledge.py` and `knowledge_management.html`:
1. `KnowledgeItem.question`/`answer` were required, but frontend sent `content`/`category`/`keywords`
2. `add_knowledge()` called non-existent `app.get_knowledge_service()` causing `AttributeError`
3. Search endpoint returned `{"data": [...]}` but frontend expected `{"results": [...]}`
4. Search endpoint returned no `query` field but frontend accessed `data.query`

**Files Changed:**
- `api/knowledge.py` — `KnowledgeItem` model now accepts optional `content`/`keywords` fields alongside `question`/`answer` for backward compatibility; `add_knowledge()` and `update_knowledge()` now use `KnowledgeService()` directly; search endpoint returns both `results` and `query` fields

#### 7. XSS Vulnerabilities in `knowledge_management.html` (P1)
**Root Cause:** All `innerHTML` insertions used unsanitized `doc.content`, `doc.category`, `doc.keywords`, and `data.query` — any knowledge entry containing `<script>` would execute in the browser.

**Files Changed:**
- `web/templates/knowledge_management.html` — Added `escapeHtml()` helper; applied escaping to all dynamic content insertions in `renderOverview()`, `renderKnowledgeList()`, `updateCategorySelects()`, and `displaySearchResults()`

#### 8. CORS Misconfiguration (P1)
**Root Cause:** `allow_origins=["*"]` combined with `allow_credentials=True` is rejected by browsers (the two are mutually exclusive under the Fetch standard).

**Files Changed:**
- `app.py` — CORS now uses explicit origin list: `["http://127.0.0.1:8000", "http://127.0.0.1:8001", "http://localhost:8000", "http://localhost:8001"]`

#### 9. Dead Code in `knowledge_management.html` (Low)
**Root Cause:** The file contained a complete duplicate of `showAlert()` and `window.onclick` outside the `</html>` closing tag, making it malformed HTML.

**Files Changed:**
- `web/templates/knowledge_management.html` — Removed duplicate code block after `</html>`

### Verification
- Server starts successfully on `http://127.0.0.1:8001`
- All initialization steps pass (knowledge base, technicians, recommendation scheduler)
- API `GET /api/user-behavior/analysis` returns valid data: `{"favorite_technician_id": 1, "favorite_service": "按摩", "favorite_duration": 180, "total_appointments": 1}`
- API `GET /api/knowledge` returns 200 with 10 documents
- API `POST /api/knowledge/search` returns `{"results": [...], "query": "...", "data": [...], "count": N, "total_found": N}` — both `results` and `query` fields present
- Pages `/admin` and `/admin/database` return 200 (templates exist)
- No linter errors introduced across all modified files
