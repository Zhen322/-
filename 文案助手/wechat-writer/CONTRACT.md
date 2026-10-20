# 字里 · 本地公众号写作工作台

Python 3.11+ stdlib ThreadingHTTPServer, SQLite, vanilla HTML/CSS/JS. Bind 127.0.0.1:8765 only. No Node build required. Actual persistent files under data/. No invented articles/metrics or fake AI results. Existing Ollama qwen2.5:latest at localhost:11434 works; default provider ollama, allow OpenAI-compatible API and Tavily configuration.

## Shared API contract
JSON requests/responses. Errors `{error: string}`, non-2xx. Dates ISO strings. No API keys ever returned; configured flags only. Local same-origin requests.

- GET /api/state -> `{articles:[],cards:[],drafts:[],notes:{text,path,revision},settings:{provider,base_url,model,search_provider,tavily_configured,api_key_configured,notes_path,profile},jobs:[],stats:{articles,cards,drafts}}`.
- GET /api/health -> `{ok:true}`.
- POST /api/articles -> `{article}`. Body `{title,content,url?,author?,tags?:[],metrics?:{reads:string,likes:string,favorites:string,source:string,observed_at:string}}`.
- PATCH /api/articles/ID -> `{article}`; same fields. DELETE -> archive, `{ok:true}`.
- POST /api/import -> `{article}`; body `{url}`; real fetch only, reject unavailable content with actionable message.
- POST /api/upload -> `{article}`; body `{filename,data}` base64; support .md .txt .html .docx (stdlib); PDF if pypdf available otherwise clear error.
- POST /api/articles/ID/analyze -> `{job_id}`; produces `article.analysis` markdown and reusable cards. Cards start approved=false.
- PATCH /api/cards/ID -> `{card}`; accepts approved boolean, title, content, category.
- POST /api/research -> `{job_id}`; body `{query,scope:'wechat'|'web',max_results:5}`; imports real search candidates; never infer metrics from search rank. Tavily officially supports capped keyless mode; try it when no API key configured, report genuine quota/network errors with configuration guidance. Public settings adds search_mode ('keyless'|'api_key'). Search candidates identify content_kind='snippet' unless full extraction succeeded.
- POST /api/drafts -> `{job_id}`; body `{brief,audience,tone,length,article_ids:[],card_ids:[],use_web:boolean}`. Read latest notes for every generation. Approved cards used by default when card_ids empty. Selection limited and reported, notes use full text up to explicit budget, must not silently truncate. Result `draft` saved as .md with references.
- PATCH /api/drafts/ID -> `{draft}`; body `{content,title?}`.
- GET /api/jobs/ID -> `{id,type,status:'running'|'done'|'error',message,result,error}`.
- GET /api/notes -> `{text,path,revision}` live-read disk; revision hash.
- PUT /api/notes -> `{text,path,revision}`; body `{text,revision}` optimistic concurrency 409 if external edit changed file. Atomic save and backup. Notes source is data/notes/我的思考.md unless configured absolute path. Existing user note must not be overwritten by selecting path.
- POST /api/open-notes -> `{ok:true}` opens current MD via macOS open (local app only).
- PUT /api/settings -> public settings; provider,base_url,model,api_key?,tavily_api_key?,notes_path?,profile?. Blank secrets preserve existing. notes_path must .md existing readable file; only user explicitly entering path selects external file. Settings stored mode 600.
- POST /api/test-model -> `{job_id}` sends tiny real model request.
- GET /api/export/drafts/ID -> downloadable Markdown.
- POST /api/drafts/ID/images -> `{job_id}` type `draft-images`; body `{count:6}` (5..8). Uses saved title/content; persists `draft.image_suggestions` and `data/drafts/ID.images.json`. Returns `{draft_id,draft}` on success. Duplicate concurrent requests for the same draft return 409; failure preserves previous image recommendations.
- GET /api/export/draft-images/ID -> downloadable image recommendation Markdown with original image links, source pages and placement/caption suggestions.
- POST /api/drafts also accepts `find_images:boolean` and `image_count:5..8`; the draft is saved before image search. Image-search failure returns successful draft with `images_error` and never discards the prose.
- `draft.image_suggestions` = `{items:[{id,image_url,source_url,source_title,description,placement,caption,query,license_note}],queries:[],warnings:[],requested_count,model,created_at,revision,stale}`. `stale` is computed against current title/content. Images remain remote; no downloaded originals or implied license grants.

## Data shapes
article = `{id,title,content,url,author,tags:[],metrics:{reads,likes,favorites,source,observed_at},source_type:'manual'|'upload'|'web',content_kind:'full'|'snippet',created_at,analysis?:string}`.
card = `{id,title,category,content,source_article_id,source_title,approved:boolean,created_at}`.
draft = `{id,title,content,references:[],created_at,model,file_path}`.
No sample data in real DB. Initial notebook may be a useful blank template, not invented user biography.

## D: publication performance records

- `GET /api/performance` -> `{records:[]}`; `/api/state` also includes `performance_records`.
- `POST /api/performance` -> `{record}`. `PATCH /api/performance/ID` requires the current `revision`; stale writes return 409 and preserve both saved data and client input.
- `POST /api/performance/import` accepts `{format:'csv'|'json',content:string}` -> `{imported,records}`. UTF-8 CSV has Chinese or English field names. JSON accepts an array or `{records:[]}`. Validate the entire batch before any insertion. Duplicate `(article_id,stats_at)` is rejected, including duplicates within a batch.
- `GET /api/performance/template.csv`, `GET /api/export/performance.csv`, and `GET /api/export/performance.json` are downloadable files. JSON export includes edit history; importing publication observations is not a replacement for full database backup restoration.
- `GET /api/performance/review` runs `performance_engine.review_records(records)` locally. It never calls a model or invents metrics.
- Each record is one article at one observation time: `{id,article_id,draft_id,published_at,stats_at,topic_type,adopted_title,title_structure,cover_template,metrics:{reads,likes,favorites,shares,comments,wows},manual_changes,change_reason,generation_seconds,model_cost,cost_currency,cost_source,account_followers_change,account_window_start,account_window_end,data_source,notes,created_at,updated_at,revision,history:[]}`.
- `article_id` is required. Other unknown values stay empty or null. Metrics are exact nonnegative integers or null; actual zero is distinct from unknown. Approximate values such as `10万+` belong in notes, not guessed numeric fields. Any provided metric requires `stats_at`; observation time cannot precede publication. Dates normalize to ISO 8601 with an offset.
- Account follower change is a signed count for the explicitly recorded account window, never an article attribution or a total across articles. Cost needs an explicit currency; absent billing evidence stays null. New drafts record actual `generation_seconds` around the prose model call, excluding subsequent image search. Existing draft timings and subscription call costs are not reconstructed.
- Edits preserve previous versions in `history`; observations at another time are separate records. Data lives in `performance_records` in SQLite and `data/performance/ID.json` / `.md`. Backups export this data from the same SQLite snapshot.
- Review deduplicates by article using the latest valid observation; it does not sum cumulative snapshots, carry old metrics forward into unknown fields, rank incompatible windows, or infer causality. Groups use one-hour observation-duration intervals. Outputs separate facts, limitations, and testable adjustments; no automatic model training or capability-card changes.

## B: evidence-first writing

- `GET /api/writing/policy` returns `{revision,text,path,original_path,mode,sources,stage_files,max_characters}`; `PUT` accepts `{revision,text,mode}`. A stale revision returns 409 without overwriting the editor text. Mode is `lite` or `deep`; the original uploaded prompt remains byte-for-byte preserved. Fixed local Markdown stage files are loaded from `data/writing-policy`, seeded once from `writing-skills/automotive-wechat-editor`. No external skill code is executed.
- Preparation captures the full policy from the server before work starts; a client-supplied `_writing_policy` is ignored. `prepared.writing_policy` stores the immutable full prompt, stage rules, mode, revision and source manifest. Every model stage reads its matching rule from this snapshot, never from later edits. Legacy snapshots without policy use the legacy pipeline. Full policy and material budgets fail explicitly rather than truncating the prompt.
- Policy-enabled runs add `publication_package` after final body/title review: `alternative_titles` (exactly four), `summary`, `cover_text`, `cover_prompt`, `share_text`, `status`, `reason`, and `review`. `writing_policy_revision` and `writing_mode` identify the snapshot. This package receives generation-time self-checks and deterministic checks; it is explicitly not independently double-reviewed. Critical body issues, incomplete audits or core evidence gaps skip the package. Package failures preserve the body and mark the task as needing attention.
- Draft edits mark both prior review and publication package stale. JSON sidecars, readable review exports and backups preserve package status and policy provenance. Backup includes source seed and editable policy/history but excludes Coze/API credentials. Style extraction uses the existing five approved-card categories and does not promote examples to facts or automatically approve rules.

- The default UI uses two explicit actions. `POST /api/writing/prepare` accepts the existing composer fields and returns `{job_id,writing_id}`. It captures the selected materials, approved cards and latest notebook, optionally researches public sources, then prepares fact cards, related writing cases and an outline. It stops at `awaiting_confirmation`; no prose is generated automatically.
- `GET /api/writing` returns `{runs}`; `GET /api/writing/ID` returns `{run}`; state includes `writing_runs`. Runs survive closing the browser and restarting the service.
- `POST /api/writing/ID/continue` requires `{revision,editor_direction?}` and returns `{job_id,writing_id}`. It uses the captured evidence, not silently refreshed material. Stale or repeated confirmation returns 409. A completed run cannot be continued again to bypass the repair limit.
- `writing_engine.prepare_writing(request,articles,cards,notes,settings,progress)` returns a versioned prepared snapshot with `request`, `personal_notes`, `notes_revision`, `sources`, `references`, `fact_cards`, `cases`, `angle`, `outline`, `gaps`, `usage`, and `model`.
- Each fact card contains `id,claim,source_id,source_quote,entity,numbers,time,event_status,confidence,caveat`. Quotes must occur verbatim in captured article text. `supported` means supported by that supplied source, never independently verified. Snippets remain uncertain; writing cards and personal viewpoints do not become fact evidence.
- `writing_engine.complete_writing(prepared,settings,editor_direction,progress,repair_budget)` runs prose, title, separate factual review, separate language review, then at most two rounds of exact-quote replacement and re-review. Repair attempts are persisted before the model call. No whole-document rewrite or invented reference is accepted as a targeted repair.
- Issues include `kind,severity,location,quote,reason,suggestion,source_ids`, plus locally computed display location. Review stores `issues,checks,rounds,repair_rounds,status,note`. Failure or invalid audit output preserves generated prose and marks it `needs_attention`. No findings still means `awaiting_review`, never automatically approved for publication.
- `GET /api/export/writing/ID` downloads a readable Markdown review package. SQLite `writing_runs` and `data/writing/ID.json/.md` preserve captured evidence, the checkpoint and audit history. A title/body edit marks the earlier review stale; exports indicate that the edited version needs human review.
- Legacy `POST /api/drafts` remains compatible. Existing drafts are not rewritten or retrospectively marked reviewed. Image search runs only after the resulting draft has been saved.

## Coze selection bridge

- Provided `code.coze.cn/p/.../preview` links are preview provenance, not deployment API endpoints or legacy workflow IDs. No project ID is converted into an invented API address.
- `POST /api/topics/import {content,source:'coze'}` parses actual pasted text/JSON locally and returns `{batch}`; `GET /api/topics` returns `{batches}`. Up to 50 topics / 200,000 characters. Batches retain the original text and full per-topic fields, without inferring missing metrics or calling a model. The user selects one topic before any writing action. SQLite `topic_batches` and `data/topics/ID.json/.md` preserve imports.
- `POST /api/topics/import {source:'coze_api',coze_job_id}` imports only the server-stored output of a successful `coze-topics` job, with a 4 MiB UTF-8 limit and at most 50 topics. Client-supplied content is ignored for this path. The batch retains the job ID and deployment address; repeated import of the same job returns its existing batch. Each topic also retains `raw_item` and a readable, complete brief. Manual import remains capped at 200,000 characters.
- `GET /api/coze/settings` returns public `{roles:{topics,writer}}`. `PUT /api/coze/settings {roles:{ROLE:{base_url,token?}}}` saves role-specific deployment settings; omitted token retains it at the same address, an explicit empty token clears it, changing an address requires new credentials. The secret is stored separately in `data/coze_secrets.json` (0600), never returned or included in backups.
- `POST /api/coze/ROLE/schema` fetches the actual deployment's `/graph_parameter` and returns `{schema}` with `input_schema` / `output_schema`. `POST /api/coze/ROLE/run {inputs:{...}}` returns a job ID; the job result preserves the remote `output` without inventing a topic or draft. Reading a schema and running a workflow are separate actions. A successful HTTP return does not imply factual verification or local B review.
- First version uses the documented Coze Coding workflow protocol: HTTPS deployment base on a `coze.site` subdomain, one direct input JSON submission to `/async_run`, then GET `/task/{task_id}` polling. It does not use the legacy `api.coze.cn/v1/workflow/run` protocol. Private destinations and redirects are rejected; tokens never follow redirects. Submissions are never automatically retried because a timed-out request may already have executed.
- Coze jobs persist `remote_task_id,remote_base_url,remote_deadline,remote_status,remote_submitted_at,poll_expires_at,can_resume`. Network or service interruption retains the remote identity. `POST /api/coze/jobs/ID/resume` explicitly resumes GET queries of that same task and returns the same local job ID; it never submits a new remote task. Polling stops at the earlier of the remote deadline or the local 24-hour cap. Unknown or failed remote status is never marked successful.
- Advanced UI builds inputs from the real input schema. `POST /api/coze/topics/quick-run` accepts only optional `topic`; `POST /api/coze/writer/quick-run` accepts a persisted `batch_id,topic_id`, optional edited `title,brief,reason`, and explicit `word_count,target_readers,author_view,materials`. Quick calls refresh the live schema and reject contract drift before submission. Defaults are from the two verified deployments; writer mode is fixed to `write_article`, and word count maps to its real string field. Selection provenance and exact submitted inputs are retained. No private notes or local policy are automatically transmitted.
- Selecting a card opens a confirmation form, never submits all candidates or automatically writes. Successful quick writer output uses actual `article` (compatible `markdown`) and preserves sources, publication package, raw result and editor notes. Imported drafts are explicitly unreviewed. Import is idempotent by job ID, preserves later user edits, and never fetches a remote download URL. `POST /api/coze/jobs/ID/import-draft` retries local persistence without another remote generation; output is persisted before local import. Advanced runs retain explicit import control.

## engine.py contract (AI agent owns)
- fetch_article(url) -> `{title,content,url,author,content_kind:'full'}`; validate public http(s), no private/local URLs, redirects revalidated, max download 5 MB; decode Chinese, extract wechat js_content or article, fail on challenges/short text.
- search_articles(query,scope,max_results,settings) -> list of `{title,content,url,author,content_kind,source_type:'web',metrics:{},tags:[]}`; uses Tavily real API only, treat snippets explicitly; best-effort full extraction capped 3 articles to limit time.
- analyze_article(article,settings) -> `{analysis:string,cards:[{title,category,content}]}`; real LLM JSON parse validated, no invented metrics, mark snippet insufficient (refuse full analysis).
- generate_draft(request,articles,cards,notes,settings) -> `{title,content,references:[],model}`; notes is `{text,path,revision}`. Selected facts cite source IDs/URLs; considers profile, original personal notes, structure cards; must not turn source facts/experiences into author's own; use_web handled by app before call (real research merged into articles). Send topic keywords to search, not the full personal notebook.
- test_model(settings) -> string.
`settings` to engine includes private api_key and tavily_api_key. All untrusted source text clearly separated as data, not instructions. Timeouts explicit and errors useful. Do not log secrets.

## Ownership
backend agent: app.py (server/storage/routes/jobs), requirements.txt if needed.
engine agent: engine.py only.
frontend agent: static/index.html, static/app.js, static/styles.css only.
root: launch scripts, README, initial notes, tests, integration fixes after coordinating.
