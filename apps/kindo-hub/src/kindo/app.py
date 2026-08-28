"""Kindo Hub 应用装配：lifespan、迁移、seed、服务接线、后台任务、中间件、mDNS。"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import secretbox
from .agent.tools import ToolRuntime
from .api import admin, health, pairing, tv, ws
from .config import Config, load_config
from .conversation.orchestrator import Orchestrator
from .conversation.service import ConversationManager
from .conversation.transition import TransitionOrchestrator
from .db import Database
from .errors import KindoError
from .history.service import HistoryService
from .logsetup import log_event, setup_logging
from .media.mounts import MountService
from .media.scanner import ScannerService
from .media.storage import StorageRegistry
from .models import AppSetting, Profile
from .playback.service import PlaybackService
from .policy.engine import PolicyEngine
from .providers.asr import AsrProviderClient
from .providers.llm import OpenAIChatCompletionsAdapter
from .providers.registry import ProviderRegistry
from .providers.tts import TtsService
from .realtime.registry import RealtimeRegistry
from .util import new_id

logger = logging.getLogger("kindo.app")


@dataclass
class AppState:
    config: Config
    db: Database
    storage: StorageRegistry
    scanner: ScannerService
    policy: PolicyEngine
    playback: PlaybackService
    history: HistoryService
    realtime: RealtimeRegistry
    conversation_manager: ConversationManager
    orchestrator: Orchestrator
    asr: AsrProviderClient
    llm: OpenAIChatCompletionsAdapter
    tts: TtsService
    admin_auth: object
    pairing: object
    mounts: MountService
    transition: TransitionOrchestrator
    provider_registry: ProviderRegistry
    instance_id: str = ""
    migrations_current: bool = False
    _extra: dict = field(default_factory=dict)

    def active_model(self) -> tuple[str | None, str | None]:
        """当前启用 Provider/Model（app_setting；缺省取第一个已配置项，含页面来源）。
        停用的 Provider 不参与解析（enabled=False：密钥保留但不进会话）。"""
        with self.db.session() as session:
            row = session.get(AppSetting, "active_model")
            if row is not None:
                pid = (row.value_json or {}).get("provider_id")
                v = self.provider_registry.get(pid or "")
                if v is not None and v.enabled:
                    return v.id, v.model
        for v in self.provider_registry.all():
            if v.enabled:
                return v.id, v.model
        return None, None


def _run_migrations(cfg: Config) -> bool:
    """启动先校验/执行 Alembic 迁移，再开放写 API（§13.1）。"""
    alembic_ini = Path(__file__).resolve().parent.parent.parent / "alembic.ini"
    aconfig = AlembicConfig(str(alembic_ini))
    aconfig.set_main_option("script_location", str(alembic_ini.parent / "alembic"))
    aconfig.attributes["db_url"] = f"sqlite:///{cfg.db_path}"
    command.upgrade(aconfig, "head")
    return True


def _seed(cfg: Config, db: Database) -> str:
    with db.session() as session:
        if session.query(Profile).count() == 0:
            session.add(Profile(id="default", display_name="default"))
        row = session.get(AppSetting, "instance_id")
        if row is None:
            instance_id = new_id()
            session.add(AppSetting(key="instance_id", value_json={"value": instance_id}))
        else:
            instance_id = (row.value_json or {}).get("value", new_id())
        session.commit()
        return instance_id


async def _background_loops(app: FastAPI) -> None:
    state: AppState = app.state.kindo
    while True:
        try:
            state.conversation_manager.sweep_idle()
            await asyncio.to_thread(state.playback.sweep_lost_playbacks)
            # Policy 执行闭环（§9.2）：硬截止到点停止 / Policy 变化立即生效
            await asyncio.to_thread(state.playback.enforce_policy_continues)
            # 成长接力（v0.3 决策七）：消费边界事件 + 时间盒收尾
            await asyncio.to_thread(state.transition.tick)
        except Exception:
            logger.exception("后台循环异常")
        await asyncio.sleep(15)


def _start_mdns(app: FastAPI) -> None:
    """mDNS 注册在后台线程执行：注册阻塞（无组播路由的环境）不得拖慢启动。"""
    state: AppState = app.state.kindo
    if not state.config.mdns_enabled:
        return

    def _register() -> None:
        try:
            from zeroconf import ServiceInfo, Zeroconf

            info = ServiceInfo(
                "_kindo._tcp.local.",
                f"{state.instance_id}._kindo._tcp.local.",
                addresses=[b"\x00" * 4],  # 占位；zeroconf 会选择实际接口地址
                port=state.config.port,
                properties={
                    "instance_id": state.instance_id,
                    "display_name": state.config.instance_display_name,
                    "api_version": "1",
                    "pairing": "true",
                },
            )
            zc = Zeroconf()
            zc.register_service(info, allow_name_change=True)
            app.state.kindo_zeroconf = zc
            log_event(logger, "mdns_registered", service="_kindo._tcp.local",
                      port=state.config.port)
        except Exception as exc:  # mDNS 失败不阻塞（可手动输入地址）
            logger.warning("mDNS 注册失败（不影响手动地址配对）: %s", exc)

    threading.Thread(target=_register, daemon=True, name="kindo-mdns").start()


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()
    setup_logging(cfg.data_dir / "logs")
    log_event(logger, "starting", config_path=str(cfg.config_path), data_dir=str(cfg.data_dir))

    _run_migrations(cfg)
    db = Database(cfg)
    secretbox.init(cfg.data_dir)  # Secret 落盘加密主密钥（先于任何 Secret 读写）
    instance_id = _seed(cfg, db)

    # 2026-08-25 全页面化决策：来源全部来自数据库；StorageRegistry 从空开始，
    # 启动时收养配置声明的旧 media_mounts（幂等，storage_id 保持原根 id）
    storage = StorageRegistry([])
    policy_engine = PolicyEngine(cfg)
    from .policy.boundary import BoundaryEventPublisher

    boundary = BoundaryEventPublisher(policy_engine._tz)
    history = HistoryService(cfg)
    realtime = RealtimeRegistry()
    playback = PlaybackService(db.session_factory, cfg, policy_engine, history,
                               notifier=realtime.emit, boundary=boundary)
    scanner = ScannerService(db.session_factory, cfg, storage)
    from .conversation.usage import ConversationUsageService

    conversation_usage = ConversationUsageService(db.session_factory)
    manager = ConversationManager(cfg, usage=conversation_usage)
    tools = ToolRuntime(db.session_factory, policy_engine, playback, history)
    tools.set_notifier(realtime.emit)
    llm = OpenAIChatCompletionsAdapter(cfg.llm_connect_timeout, cfg.llm_first_event_timeout,
                                       cfg.llm_total_timeout)
    tts = TtsService()
    asr = AsrProviderClient(cfg.asr_endpoint, cfg.asr_timeout_seconds)

    from .admin.auth import AdminAuthService
    from .pairing import PairingService

    admin_auth = AdminAuthService(cfg)
    pairing_svc = PairingService()
    mounts_svc = MountService(storage, db.session_factory, cfg)
    provider_registry = ProviderRegistry(cfg, db.session_factory)
    from .media.scrape import ScrapeService

    scrape_svc = ScrapeService(cfg, db.session_factory)
    from .conversation.transition import TransitionOrchestrator

    transition = TransitionOrchestrator(
        cfg, db.session_factory, policy_engine, boundary,
        notifier=realtime.emit, playback=playback,
        provider_resolver=lambda pid: provider_registry.get(pid))

    state = AppState(
        config=cfg, db=db, storage=storage, scanner=scanner, policy=policy_engine,
        playback=playback, history=history, realtime=realtime, conversation_manager=manager,
        orchestrator=None,  # type: ignore[arg-type]
        asr=asr, llm=llm, tts=tts, admin_auth=admin_auth, pairing=pairing_svc,
        mounts=mounts_svc, transition=transition, provider_registry=provider_registry,
        instance_id=instance_id, migrations_current=True,
    )
    state._extra["scrape"] = scrape_svc
    # 家长 AI 助手（PRD 8.14 AIA / 架构 A-18~A-20 / 技术方案 §19，实施计划 S1）：
    # AI Runtime 与 Job Runner 均为本进程内部模块，不新增部署单元
    from .ai.jobs import AiJobRunner
    from .ai.proposals import ProposalService
    from .ai.runtime import LLMRuntime

    ai_proposals = ProposalService(db.session_factory, cfg, storage,
                                    policy_engine=policy_engine, playback=playback)
    ai_runtime = LLMRuntime(llm, db.session_factory, provider_registry)
    state._extra["ai_proposals"] = ai_proposals
    state._extra["ai_jobs"] = AiJobRunner(db.session_factory, cfg, ai_runtime,
                                          ai_proposals, storage,
                                          history=history, policy=policy_engine,
                                          playback=playback)
    orchestrator = Orchestrator(
        cfg, manager, tools, llm, tts, realtime, db.session_factory,
        playback, policy_engine, history, provider_registry.get,
    )
    state.orchestrator = orchestrator

    app = FastAPI(title="Kindo Hub", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.kindo = state

    # ---------- 中间件：request_id + 统一错误 envelope（§2.3/§15.2） ----------
    @app.middleware("http")
    async def envelope_middleware(request: Request, call_next):
        request_id = new_id()
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except KindoError as exc:
            response = JSONResponse(status_code=exc.http_status,
                                    content=exc.envelope(request_id))
        except Exception:
            logger.exception("未处理异常 path=%s", request.url.path)
            err = KindoError("internal_error", 500, "服务内部错误")
            response = JSONResponse(status_code=500, content=err.envelope(request_id))
        response.headers["X-Request-Id"] = request_id
        return response

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(KindoError)
    async def kindo_error_handler(request: Request, exc: KindoError):
        request_id = getattr(request.state, "request_id", new_id())
        body = exc.envelope(request_id)
        if exc.code == "policy_denied":
            body["decision"] = "deny"
            body["reason_code"] = exc.reason_code
            body["constraints"] = exc.constraints or {}
        return JSONResponse(status_code=exc.http_status, content=body)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", new_id())
        err = KindoError("invalid_request", 400, "请求参数不合法",
                         details={"errors": [str(e)[:200] for e in exc.errors()[:5]]})
        return JSONResponse(status_code=400, content=err.envelope(request_id))

    app.include_router(health.router)
    app.include_router(tv.router)
    app.include_router(pairing.router)
    app.include_router(admin.router)
    app.include_router(ws.router)

    # ---------- Web Admin 静态资源（构建产物并入 hub，§1 技术栈） ----------
    admin_dist = Path(__file__).resolve().parent.parent.parent / "admin_dist"
    if admin_dist.is_dir():
        app.mount("/admin", StaticFiles(directory=str(admin_dist), html=True), name="admin")
    else:
        @app.get("/admin")
        def admin_not_built():
            return JSONResponse({
                "note": "Web Admin 前端未构建",
                "how_to_build": "cd apps/kindo-admin && npm install && npm run build "
                                "&& cp -r dist ../kindo-hub/admin_dist",
            })

    # ---------- lifespan ----------
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        state.realtime.bind_loop(asyncio.get_running_loop())
        with db.session() as session:
            from .models import AdminUser

            has_admin = session.query(AdminUser).count() > 0
            source = admin_auth.ensure_bootstrap_material(has_admin)
            if source:
                log_event(logger, "admin_bootstrap_pending", source=source)
        scanner.mark_interrupted_on_startup()
        state._extra["ai_jobs"].mark_interrupted_on_startup()
        # 常规对话计量：收尾上次进程未闭合的会话行（crash-safe）
        orphans = conversation_usage.finalize_orphans(cfg.session_idle_seconds)
        if orphans:
            log_event(logger, "conversation_usage_finalized", count=orphans)
        # Secret 落盘加密巡检：历史明文行统一转密文（幂等）
        with db.session() as session:
            encrypted_legacy = secretbox.encrypt_legacy_secrets(session)
            if encrypted_legacy:
                log_event(logger, "secrets_encrypted_at_rest", count=encrypted_legacy)
        # 收养配置声明的 LLM Provider（全页面化决策：唯一事实来源=数据库）
        with db.session() as session:
            adopted_providers = provider_registry.adopt_config_providers(session)
            if adopted_providers:
                provider_registry.reload()
                log_event(logger, "config_providers_adopted", count=adopted_providers)
        # ASR 热词（ASR-005）：文件不存在时从库生成首版（此后由页面手动重建，
        # 避免 boot 即覆盖家长手工补写；"## manual" 段重建时保留）
        from . import asr_words

        if not asr_words.hotwords_path(cfg).exists():
            try:
                with db.session() as session:
                    built = asr_words.write_hotwords(cfg, session)
                log_event(logger, "asr_hotwords_built", count=built["count"])
            except Exception as exc:  # 热词失败不阻塞启动
                logger.warning("asr hotwords build failed: %s", exc)
        # 收养配置声明的外层根（升级路径，幂等）→ 恢复页面注册的来源
        with db.session() as session:
            adopted = mounts_svc.adopt_config_roots(session)
            if adopted:
                log_event(logger, "config_roots_adopted", count=adopted)
            restored = mounts_svc.restore_active_mounts(session)
            if restored:
                log_event(logger, "page_mounts_restored", count=restored)
        recovered = playback.recover_on_startup()
        if recovered:
            log_event(logger, "playback_recovered_on_restart", count=recovered)
        _start_mdns(app)
        bg = asyncio.create_task(_background_loops(app))
        log_event(logger, "hub_started", port=cfg.port, mounts=storage.mount_ids)
        try:
            yield
        finally:
            bg.cancel()
            orchestrator.shutdown()
            zc = getattr(app.state, "kindo_zeroconf", None)
            if zc is not None:
                with contextlib.suppress(Exception):
                    zc.close()
            await asr.aclose()

    app.router.lifespan_context = lifespan
    return app


def main() -> None:
    """python -m kindo → 启动 Hub。"""
    import uvicorn

    cfg = load_config()
    app = create_app(cfg)
    uvicorn.run(app, host=cfg.bind, port=cfg.port, log_config=None)
