"""English command-line interface for offline"""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, TypeVar

import click
import typer
import yaml
from click.core import ParameterSource

from aruodas_scraper.cities import load_city_registry
from aruodas_scraper.constants import (
    DEFAULT_CACHE_DIRECTORY,
    DEFAULT_CHECKPOINT,
    DEFAULT_OUTPUT_DIRECTORY,
    DEFAULT_RUN_CONFIG,
)
from aruodas_scraper.exceptions import ConfigurationError, RetrievalError
from aruodas_scraper.logging_config import configure_logging
from aruodas_scraper.networking.browser_profile import DEFAULT_USER_AGENT, navigation_headers
from aruodas_scraper.networking.budget import DEFAULT_MAX_COOLDOWNS, DEFAULT_MAX_EMPTY_BURSTS
from aruodas_scraper.networking.cache import HtmlCache
from aruodas_scraper.networking.cookie_minter import (
    DEFAULT_MINT_TIMEOUT_SECONDS,
    DEFAULT_MINT_URL,
    mint_cookie,
)
from aruodas_scraper.networking.cookie_source import (
    PROTECTION_COOKIE_NAME,
    BrowserCookie,
    load_cookie_file,
    write_cookie_file,
)
from aruodas_scraper.networking.curl_fetcher import DEFAULT_IMPERSONATION
from aruodas_scraper.networking.http_client import AruodasHttpClient, FetchOptions, build_fetcher
from aruodas_scraper.networking.rate_limiter import (
    DEFAULT_JITTER_SECONDS,
    DEFAULT_MINIMUM_DELAY_SECONDS,
    DEFAULT_PAUSE_PROBABILITY,
    DelayPolicy,
)
from aruodas_scraper.networking.tls import resolve_tls_trust
from aruodas_scraper.pipelines.all_properties import process_offline
from aruodas_scraper.pipelines.online import DEFAULT_RETRY_COOLDOWN_SECONDS, process_online
from aruodas_scraper.run_config import RunConfig, load_run_config
from aruodas_scraper.validation.records import validate_csv

app = typer.Typer(help="Parse Aruodas HTML into normalized English datasets.")

_T = TypeVar("_T")

_CONFIG_OPTION = typer.Option(
    "--config",
    help=f"Run configuration file. Defaults to {DEFAULT_RUN_CONFIG} when it exists.",
)
_NO_CONFIG_OPTION = typer.Option("--no-config", help="Ignore any run configuration file.")

_DOCTOR_ROBOTS_URL = "https://www.aruodas.lt/robots.txt"
_DOCTOR_SEARCH_URL = "https://www.aruodas.lt/butai/vilniuje/"


def _effective_config(config: Path | None, no_config: bool) -> RunConfig | None:
    """Resolve the run configuration, honouring explicit paths and auto-discovery."""
    if no_config:
        return None
    if config is not None:
        return load_run_config(config)
    discovered = Path(DEFAULT_RUN_CONFIG)
    return load_run_config(discovered) if discovered.is_file() else None


def _resolve(name: str, cli_value: _T, config_value: _T | None) -> _T:
    """Return the CLI value when explicitly given, else the configured value."""
    context = click.get_current_context(silent=True)
    if context is not None and context.get_parameter_source(name) is ParameterSource.COMMANDLINE:
        return cli_value
    return cli_value if config_value is None else config_value


def _warn_about_cookie(cookie: BrowserCookie | None) -> None:
    """Say what the borrowed session is worth, before a run spends an hour finding out."""
    if cookie is None:
        typer.echo("Cookie:     none")
        return
    typer.echo(f"Cookie:     {cookie.describe()}")
    if not cookie.has_protection_token:
        typer.echo(
            f"  Warning: the cookie file has no {PROTECTION_COOKIE_NAME}, so it does not "
            "carry a scored session and will not raise the request ceiling. Re-copy it from "
            "a browser that has loaded an Aruodas page.",
            err=True,
        )
    elif cookie.is_stale:
        typer.echo(
            "  Warning: the cookie file was copied over an hour ago and has probably been "
            "rotated. An expired cookie behaves exactly like no cookie at all.",
            err=True,
        )


_COMMAND_DEFAULTS: dict[str, dict[str, object]] = {
    "scrape-live": {
        "cities_config": "config/cities.yaml",
        "city": "vilnius",
        "property_type": "all",
        "output": DEFAULT_OUTPUT_DIRECTORY,
        "cache": DEFAULT_CACHE_DIRECTORY,
        "max_pages": 1,
        "max_listings_per_category": 20,
        "timeout_seconds": 30.0,
        "min_delay_seconds": DEFAULT_MINIMUM_DELAY_SECONDS,
        "jitter_seconds": DEFAULT_JITTER_SECONDS,
        "retry_cooldown_seconds": DEFAULT_RETRY_COOLDOWN_SECONDS,
        "max_cooldowns": DEFAULT_MAX_COOLDOWNS,
        "max_empty_bursts": DEFAULT_MAX_EMPTY_BURSTS,
        "max_runtime_seconds": None,
        "solve_on_block": False,
        "refresh_cache": False,
        "overwrite": False,
        "deepen": True,
        "user_agent": DEFAULT_USER_AGENT,
        "ca_bundle": None,
        "proxy": None,
        "http2": False,
        "transport": "curl",
        "impersonate": DEFAULT_IMPERSONATION,
        "cookie_file": None,
    },
    "parse-offline": {
        "input": None,
        "property_type": "all",
        "city": "vilnius",
        "output": DEFAULT_OUTPUT_DIRECTORY,
        "checkpoint": None,
        "resume": False,
        "refresh": False,
        "translate": False,
    },
}


def _effective_settings(command: str) -> dict[str, object]:
    """Merge command defaults with any discovered run configuration."""
    settings = dict(_COMMAND_DEFAULTS[command])
    discovered = _effective_config(None, no_config=False)
    if discovered is not None:
        section = discovered.scrape_live if command == "scrape-live" else discovered.parse_offline
        settings.update(
            {name: value for name, value in section if value is not None},
        )
    return {
        name: str(value) if isinstance(value, Path) else value for name, value in settings.items()
    }


@app.callback(invoke_without_command=True)
def main(context: typer.Context) -> None:
    """Parse Aruodas HTML into normalized English datasets."""
    configure_logging()
    if context.invoked_subcommand is not None:
        return
    try:
        settings = _effective_config(None, no_config=False)
    except ConfigurationError as error:
        typer.echo(f"Run configuration failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    command = settings.default_command if settings else None
    if command is None:
        typer.echo(context.get_help())
        raise typer.Exit(code=0)
    typer.echo(f"Running {command} from {DEFAULT_RUN_CONFIG}.")
    if command == "scrape-live":
        scrape_live_command()
    else:
        parse_offline_command()


@app.command("parse-offline")
def parse_offline_command(
    input_directory: Annotated[
        Path | None, typer.Option("--input", exists=True, file_okay=False)
    ] = None,
    property_type: Annotated[str, typer.Option(help="apartments, houses, or all")] = "all",
    city: Annotated[str, typer.Option()] = "vilnius",
    output: Annotated[Path, typer.Option()] = Path(DEFAULT_OUTPUT_DIRECTORY),
    checkpoint: Annotated[Path | None, typer.Option()] = None,
    resume: Annotated[bool, typer.Option()] = False,
    refresh: Annotated[bool, typer.Option()] = False,
    translate: Annotated[bool, typer.Option(help="Reserved for a configured provider.")] = False,
    config: Annotated[Path | None, _CONFIG_OPTION] = None,
    no_config: Annotated[bool, _NO_CONFIG_OPTION] = False,
) -> None:
    """Parse listing-detail HTML files from a local directory."""
    try:
        settings = _effective_config(config, no_config)
    except ConfigurationError as error:
        typer.echo(f"Offline parse failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    configured = settings.parse_offline if settings else None

    input_directory = _resolve(
        "input_directory", input_directory, configured.input if configured else None
    )
    property_type = _resolve(
        "property_type", property_type, configured.property_type if configured else None
    )
    city = _resolve("city", city, configured.city if configured else None)
    output = _resolve("output", output, configured.output if configured else None)
    checkpoint = _resolve("checkpoint", checkpoint, configured.checkpoint if configured else None)
    resume = _resolve("resume", resume, configured.resume if configured else None)
    refresh = _resolve("refresh", refresh, configured.refresh if configured else None)
    translate = _resolve("translate", translate, configured.translate if configured else None)

    if input_directory is None:
        raise typer.BadParameter("--input is required unless the run configuration provides it")
    if not input_directory.is_dir():
        raise typer.BadParameter(f"--input directory does not exist: {input_directory}")
    if property_type not in {"apartments", "houses", "all"}:
        raise typer.BadParameter("property-type must be apartments, houses, or all")
    if translate:
        typer.echo("Translation is disabled because no provider is configured.")
    effective_checkpoint = checkpoint or Path(DEFAULT_CHECKPOINT).with_name(
        f"{city}_{property_type}.json"
    )
    summary = process_offline(
        input_directory,
        output,
        city,
        property_type,
        effective_checkpoint,
        resume=resume,
        refresh=refresh,
    )
    typer.echo(
        f"Exported {summary.apartments_exported} apartment(s) and "
        f"{summary.houses_exported} house(s). Failed: {summary.failed}."
    )


@app.command("scrape-live")
def scrape_live_command(
    cities_config: Annotated[
        Path, typer.Option("--cities-config", exists=True, dir_okay=False)
    ] = Path("config/cities.yaml"),
    city: Annotated[str, typer.Option()] = "vilnius",
    property_type: Annotated[str, typer.Option(help="apartments, houses, or all")] = "all",
    output: Annotated[Path, typer.Option()] = Path(DEFAULT_OUTPUT_DIRECTORY),
    cache: Annotated[Path, typer.Option()] = Path(DEFAULT_CACHE_DIRECTORY),
    max_pages: Annotated[int, typer.Option(min=1, max=20)] = 1,
    max_listings_per_category: Annotated[int, typer.Option(min=1, max=500)] = 20,
    timeout_seconds: Annotated[float, typer.Option(min=1.0, max=120.0)] = 30.0,
    min_delay_seconds: Annotated[
        float,
        typer.Option(
            min=0.0,
            max=600.0,
            help="Mean gap between requests; the actual wait is this give or take "
            "--jitter-seconds. Raise it to pace a run more slowly than the default.",
        ),
    ] = DEFAULT_MINIMUM_DELAY_SECONDS,
    jitter_seconds: Annotated[
        float,
        typer.Option(
            min=0.0,
            max=60.0,
            help="Half-width of the random band around --min-delay-seconds, so 5 with a "
            "jitter of 2 waits between 3 and 7 seconds. Cannot exceed the minimum delay.",
        ),
    ] = DEFAULT_JITTER_SECONDS,
    retry_cooldown_seconds: Annotated[
        float,
        typer.Option(
            min=0.0,
            max=3600.0,
            help="Wait this long whenever the origin starts refusing, then carry on. "
            "Blocks are per-IP and self-clearing, so waiting is what restores access. "
            "0 disables waiting, ending the run at the first block.",
        ),
    ] = DEFAULT_RETRY_COOLDOWN_SECONDS,
    max_cooldowns: Annotated[
        int,
        typer.Option(
            min=0,
            max=20,
            help="How many blocks the run may wait out before giving up. Each one costs "
            "--retry-cooldown-seconds of wall clock, so this bounds the total run length.",
        ),
    ] = DEFAULT_MAX_COOLDOWNS,
    max_empty_bursts: Annotated[
        int,
        typer.Option(
            min=1,
            max=10,
            help="Give up once this many consecutive bursts have served nothing. A block "
            "that is not lapsing then costs one cooldown instead of the whole allowance.",
        ),
    ] = DEFAULT_MAX_EMPTY_BURSTS,
    max_runtime_seconds: Annotated[
        float | None,
        typer.Option(
            min=1.0,
            help="Stop the run once it has been going this long, cooldowns included. "
            "A cooldown that would overrun the limit is not started at all.",
        ),
    ] = None,
    solve_on_block: Annotated[
        bool,
        typer.Option(
            help="On a block, open a browser and wait for you to solve the challenge instead "
            "of waiting out the cooldown. Solving clears the block immediately, so this "
            "turns a 25-minute wait into a click. Needs someone at the keyboard.",
        ),
    ] = False,
    refresh_cache: Annotated[bool, typer.Option()] = False,
    overwrite: Annotated[
        bool,
        typer.Option(help="Re-fetch listings already present in the export."),
    ] = False,
    deepen: Annotated[
        bool,
        typer.Option(
            help=(
                "Spend the run on detail pages for listings the export holds only as search "
                "cards, skipping the search walk. Use --no-deepen to discover new listings."
            )
        ),
    ] = True,
    allow_stale_cookie: Annotated[
        bool,
        typer.Option(
            help="Start even when the cookie file is old enough to have been rotated. "
            "Off by default, because a run that waits out cooldowns on a spent cookie "
            "spends hours to collect almost nothing.",
        ),
    ] = False,
    user_agent: Annotated[
        str,
        typer.Option(help="Identity sent to Aruodas; use the string they allow-listed."),
    ] = DEFAULT_USER_AGENT,
    ca_bundle: Annotated[
        Path | None,
        typer.Option(help="CA bundle for a TLS-intercepting corporate proxy."),
    ] = None,
    proxy: Annotated[
        str | None,
        typer.Option(help="Proxy URL, including credentials when the proxy requires them."),
    ] = None,
    http2: Annotated[
        bool, typer.Option(help="Negotiate HTTP/2 when available. Ignored by the curl transport.")
    ] = False,
    transport: Annotated[
        str,
        typer.Option(help="curl impersonates a real Chrome fingerprint; httpx does not."),
    ] = "curl",
    impersonate: Annotated[
        str,
        typer.Option(help="curl_cffi Chrome profile, for example chrome131."),
    ] = DEFAULT_IMPERSONATION,
    cookie_file: Annotated[
        Path | None,
        typer.Option(
            help="File holding a Cookie header copied from a signed-in browser on this network. "
            "Keep it outside the repository.",
        ),
    ] = None,
    config: Annotated[Path | None, _CONFIG_OPTION] = None,
    no_config: Annotated[bool, _NO_CONFIG_OPTION] = False,
) -> None:
    """Retrieve and parse a bounded live Aruodas snapshot."""
    try:
        settings = _effective_config(config, no_config)
    except ConfigurationError as error:
        typer.echo(f"Live scrape failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    configured = settings.scrape_live if settings else None

    cities_config = _resolve(
        "cities_config", cities_config, configured.cities_config if configured else None
    )
    city = _resolve("city", city, configured.city if configured else None)
    property_type = _resolve(
        "property_type", property_type, configured.property_type if configured else None
    )
    output = _resolve("output", output, configured.output if configured else None)
    cache = _resolve("cache", cache, configured.cache if configured else None)
    max_pages = _resolve("max_pages", max_pages, configured.max_pages if configured else None)
    max_listings_per_category = _resolve(
        "max_listings_per_category",
        max_listings_per_category,
        configured.max_listings_per_category if configured else None,
    )
    timeout_seconds = _resolve(
        "timeout_seconds", timeout_seconds, configured.timeout_seconds if configured else None
    )
    min_delay_seconds = _resolve(
        "min_delay_seconds",
        min_delay_seconds,
        configured.min_delay_seconds if configured else None,
    )
    jitter_seconds = _resolve(
        "jitter_seconds", jitter_seconds, configured.jitter_seconds if configured else None
    )
    retry_cooldown_seconds = _resolve(
        "retry_cooldown_seconds",
        retry_cooldown_seconds,
        configured.retry_cooldown_seconds if configured else None,
    )
    max_cooldowns = _resolve(
        "max_cooldowns", max_cooldowns, configured.max_cooldowns if configured else None
    )
    max_empty_bursts = _resolve(
        "max_empty_bursts", max_empty_bursts, configured.max_empty_bursts if configured else None
    )
    solve_on_block = _resolve(
        "solve_on_block", solve_on_block, configured.solve_on_block if configured else None
    )
    max_runtime_seconds = _resolve(
        "max_runtime_seconds",
        max_runtime_seconds,
        configured.max_runtime_seconds if configured else None,
    )
    refresh_cache = _resolve(
        "refresh_cache", refresh_cache, configured.refresh_cache if configured else None
    )
    overwrite = _resolve("overwrite", overwrite, configured.overwrite if configured else None)
    deepen = _resolve("deepen", deepen, configured.deepen if configured else None)
    user_agent = _resolve("user_agent", user_agent, configured.user_agent if configured else None)
    ca_bundle = _resolve("ca_bundle", ca_bundle, configured.ca_bundle if configured else None)
    proxy = _resolve("proxy", proxy, configured.proxy if configured else None)
    http2 = _resolve("http2", http2, configured.http2 if configured else None)
    transport = _resolve("transport", transport, configured.transport if configured else None)
    impersonate = _resolve(
        "impersonate", impersonate, configured.impersonate if configured else None
    )
    cookie_file = _resolve(
        "cookie_file", cookie_file, configured.cookie_file if configured else None
    )

    if property_type not in {"apartments", "houses", "all"}:
        raise typer.BadParameter("property-type must be apartments, houses, or all")
    if transport not in {"curl", "httpx"}:
        raise typer.BadParameter("transport must be curl or httpx")
    if jitter_seconds > min_delay_seconds:
        raise typer.BadParameter(
            f"--jitter-seconds ({jitter_seconds}) cannot exceed --min-delay-seconds "
            f"({min_delay_seconds}); the delay band would reach below zero"
        )
    try:
        registry = load_city_registry(cities_config)
        registry.get_city(city)
        if solve_on_block and cookie_file is None:
            typer.echo(
                "  --solve-on-block needs cookie_file set: the renewed session has to be "
                "written somewhere, and the browser profile that remembers your solves "
                "lives beside it.",
                err=True,
            )
            raise typer.Exit(code=1)
        cookie = load_cookie_file(cookie_file)
        _warn_about_cookie(cookie)
        # A stale cookie is only fatal when waiting is the run's only recovery. With
        # --solve-on-block the run can re-earn the session the moment the origin objects,
        # so starting on an old cookie costs one block rather than hours of cooldowns.
        if (
            cookie is not None
            and cookie.is_stale
            and not allow_stale_cookie
            and not solve_on_block
            and max_cooldowns > 0
        ):
            typer.echo(
                f"  Refusing to start: this run may wait out up to {max_cooldowns} cooldown(s) "
                "on a cookie that has probably already been rotated, which is hours spent for "
                "the request ceiling of no cookie at all. Re-copy it, or pass "
                "--allow-stale-cookie to run anyway.",
                err=True,
            )
            raise typer.Exit(code=1)
        with AruodasHttpClient(
            cache=HtmlCache(cache),
            delay_policy=DelayPolicy.centred(
                min_delay_seconds, jitter_seconds, DEFAULT_PAUSE_PROBABILITY
            ),
            options=FetchOptions(
                timeout_seconds=timeout_seconds,
                user_agent=user_agent,
                ca_bundle=ca_bundle,
                proxy=proxy,
                http2=http2,
                transport=transport,  # type: ignore[arg-type]
                impersonate=impersonate,
                cookie=cookie.header if cookie else None,
            ),
        ) as client:
            summary = process_online(
                city=city,
                property_type=property_type,
                client=client,
                city_registry=registry,
                output_directory=output,
                max_pages=max_pages,
                max_listings_per_category=max_listings_per_category,
                refresh_cache=refresh_cache,
                overwrite=overwrite,
                deepen=deepen,
                retry_cooldown_seconds=retry_cooldown_seconds,
                max_cooldowns=max_cooldowns,
                max_empty_bursts=max_empty_bursts,
                max_runtime_seconds=max_runtime_seconds,
                renewer=(
                    _build_session_renewer(client, cookie_file)
                    if solve_on_block and cookie_file is not None
                    else None
                ),
            )
    except (ConfigurationError, RetrievalError, OSError, ValueError) as error:
        typer.echo(f"Live scrape failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    recovered = ""
    if summary.deferred_retries_attempted:
        recovered = (
            f" Recovered {summary.deferred_retries_recovered} of "
            f"{summary.deferred_retries_attempted} blocked listing(s) after the cooldown."
        )
    typer.echo(
        f"Exported {summary.apartments_exported} apartment(s) and "
        f"{summary.houses_exported} house(s). "
        f"Skipped {summary.skipped_existing} already-exported listing(s). "
        f"Failed: {summary.failed}.{recovered}"
    )


def _build_session_renewer(client: AruodasHttpClient, cookie_file: Path) -> Callable[[], bool]:
    """Return the callback a blocked run uses to re-earn its session instead of waiting.

    Waiting out a block restores the same small budget it just spent; solving a challenge
    restores a much larger one and does it now. So the browser is opened at the moment the
    origin objects, rather than leaving the operator to notice a stalled run.
    """

    def renew() -> bool:
        typer.echo("")
        typer.echo("  Blocked. Opening a browser so the block can be cleared now.")
        try:
            minted = mint_cookie(
                profile_dir=cookie_file.parent / "browser_profile",
                on_challenge=_announce_challenge,
            )
        except ConfigurationError as error:
            # A failed renewal must not end the run: the cooldown is still there to fall
            # back on, and finishing slowly beats not finishing.
            typer.echo(f"  Could not renew the session ({error}).", err=True)
            typer.echo("  Falling back to waiting out the block.", err=True)
            return False
        write_cookie_file(cookie_file, minted.header)
        client.set_cookie(minted.header)
        typer.echo(f"  Session renewed ({minted.describe()}). Continuing with no cooldown.")
        typer.echo("")
        return True

    return renew


def _announce_challenge() -> None:
    """Tell the operator to solve the challenge, and why it is worth doing by hand."""
    typer.echo("")
    typer.echo("  A challenge appeared. Solve it in the browser window that just opened.")
    typer.echo("  This is the step that buys the request budget: a cookie minted after a")
    typer.echo("  solved challenge has been measured at 100+ requests, against about 6 for")
    typer.echo("  one taken from an ordinary browse. Waiting for you...")
    typer.echo("")


@app.command("mint-cookie")
def mint_cookie_command(
    output: Annotated[
        Path | None,
        typer.Option(help="Where to write the cookie. Defaults to cookie_file from the config."),
    ] = None,
    url: Annotated[str, typer.Option(help="Page to open in the browser.")] = DEFAULT_MINT_URL,
    profile_dir: Annotated[
        Path | None,
        typer.Option(help="Chrome profile that remembers solves. Defaults beside the cookie."),
    ] = None,
    timeout_seconds: Annotated[
        float, typer.Option(min=10.0, max=1800.0, help="How long to wait for a solve.")
    ] = DEFAULT_MINT_TIMEOUT_SECONDS,
    config: Annotated[Path | None, _CONFIG_OPTION] = None,
    no_config: Annotated[bool, _NO_CONFIG_OPTION] = False,
) -> None:
    """Open a browser, wait for the challenge to clear, and save the session cookie.

    Replaces copying a Cookie header out of DevTools by hand. The browser is visible on
    purpose: the challenge has to be solved by a person, and that solve is precisely what
    raises the ceiling for the run that follows.
    """
    try:
        settings = _effective_config(config, no_config)
    except ConfigurationError as error:
        typer.echo(f"Minting failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    configured = settings.scrape_live if settings else None

    output = _resolve("cookie_file", output, configured.cookie_file if configured else None)
    if output is None:
        typer.echo(
            "Nowhere to write the cookie: pass --output, or set cookie_file in the config.",
            err=True,
        )
        raise typer.Exit(code=1)
    # Beside the cookie by default, which is already outside the repository and gitignored.
    # The profile holds a live session and must not land anywhere that gets committed.
    resolved_profile = profile_dir if profile_dir is not None else output.parent / "browser_profile"

    typer.echo(f"Opening:  {url}")
    typer.echo(f"Profile:  {resolved_profile}")
    try:
        minted = mint_cookie(
            profile_dir=resolved_profile,
            url=url,
            timeout_seconds=timeout_seconds,
            on_challenge=_announce_challenge,
        )
        write_cookie_file(output, minted.header)
    except ConfigurationError as error:
        typer.echo(f"Minting failed: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Saved:    {output} ({minted.describe()})")
    if not minted.solved_challenge:
        typer.echo("No challenge was raised; the saved profile was still trusted.")


@app.command("doctor")
def doctor_command(
    url: Annotated[
        str, typer.Option(help="Search page used for the reachability check.")
    ] = _DOCTOR_SEARCH_URL,
    user_agent: Annotated[str, typer.Option()] = DEFAULT_USER_AGENT,
    ca_bundle: Annotated[Path | None, typer.Option()] = None,
    proxy: Annotated[str | None, typer.Option()] = None,
    timeout_seconds: Annotated[float, typer.Option(min=1.0, max=120.0)] = 30.0,
    transport: Annotated[str, typer.Option()] = "curl",
    impersonate: Annotated[str, typer.Option()] = DEFAULT_IMPERSONATION,
    cookie_file: Annotated[
        Path | None,
        typer.Option(help="Probe with this browser session cookie, to check it is still valid."),
    ] = None,
    config: Annotated[Path | None, _CONFIG_OPTION] = None,
    no_config: Annotated[bool, _NO_CONFIG_OPTION] = False,
) -> None:
    """Report how the scraper will connect, then check that Aruodas is reachable."""
    try:
        settings = _effective_config(config, no_config)
    except ConfigurationError as error:
        typer.echo(f"Diagnostics failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    configured = settings.scrape_live if settings else None

    user_agent = _resolve("user_agent", user_agent, configured.user_agent if configured else None)
    ca_bundle = _resolve("ca_bundle", ca_bundle, configured.ca_bundle if configured else None)
    proxy = _resolve("proxy", proxy, configured.proxy if configured else None)
    transport = _resolve("transport", transport, configured.transport if configured else None)
    impersonate = _resolve(
        "impersonate", impersonate, configured.impersonate if configured else None
    )
    cookie_file = _resolve(
        "cookie_file", cookie_file, configured.cookie_file if configured else None
    )
    if transport not in {"curl", "httpx"}:
        raise typer.BadParameter("transport must be curl or httpx")

    try:
        trust = resolve_tls_trust(ca_bundle)
        cookie = load_cookie_file(cookie_file)
        options = FetchOptions(
            timeout_seconds=timeout_seconds,
            user_agent=user_agent,
            ca_bundle=ca_bundle,
            proxy=proxy,
            http2=False,
            transport=transport,  # type: ignore[arg-type]
            impersonate=impersonate,
            cookie=cookie.header if cookie else None,
        )
        options.validate()
    except (ConfigurationError, ValueError) as error:
        typer.echo(f"Diagnostics failed: {error}", err=True)
        raise typer.Exit(code=1) from error

    environment_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    typer.echo(f"TLS trust:  {trust.source}")
    typer.echo(f"Proxy:      {proxy or environment_proxy or 'none'}")
    typer.echo(f"Transport:  {transport}" + (f" ({impersonate})" if transport == "curl" else ""))
    identity = user_agent if transport == "httpx" else "from impersonation profile"
    typer.echo(f"User-Agent: {identity}")
    _warn_about_cookie(cookie)

    failures = 0
    fetcher = build_fetcher(options, trust)
    try:
        for label, target in (("robots.txt ", _DOCTOR_ROBOTS_URL), ("search page", url)):
            try:
                response = fetcher.fetch_page(target, navigation_headers(None))
            except RetrievalError as error:
                failures += 1
                typer.echo(f"{label}: FAILED - {error}", err=True)
                if "CERTIFICATE_VERIFY_FAILED" in str(error):
                    typer.echo(
                        "  Hint: a TLS-intercepting proxy is in the path. Point 'ca_bundle' "
                        "at its CA certificate, or set SSL_CERT_FILE.",
                        err=True,
                    )
                continue
            typer.echo(f"{label}: HTTP {response.status_code} ({len(response.body)} bytes)")
            if response.status_code >= 400:
                failures += 1
            if response.status_code == 407:
                typer.echo(
                    "  Hint: the proxy wants credentials. Set 'proxy' to a URL that "
                    "includes them, or run off the corporate VPN.",
                    err=True,
                )
            elif response.status_code == 403 or b"px-captcha" in response.body:
                failures += 1 if response.status_code < 400 else 0
                typer.echo(
                    "  Hint: bot protection rejected this client. Set 'transport: curl' in "
                    "config/scrape.yaml, or try another 'impersonate' profile.",
                    err=True,
                )
    finally:
        fetcher.close()

    if failures:
        raise typer.Exit(code=1)
    typer.echo("Connectivity looks healthy.")


@app.command("validate")
def validate_command(
    csv_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Validate an exported apartment or house CSV file."""
    result = validate_csv(csv_path)
    if result.duplicate_listing_ids:
        typer.echo(f"Validation failed: {result.duplicate_listing_ids} duplicate listing ID(s).")
        raise typer.Exit(code=1)
    noun = "record" if result.total_records == 1 else "records"
    typer.echo(f"Validated {result.total_records} valid {noun}; no duplicate listing IDs.")


@app.command("report-unknown-fields")
def report_unknown_fields(
    report: Annotated[Path, typer.Option()] = Path("data/processed/unknown_fields.csv"),
) -> None:
    """Print the current unknown-field report path and contents."""
    if not report.exists():
        typer.echo(f"Unknown-field report does not exist: {report}")
        raise typer.Exit(code=1)
    typer.echo(report.read_text(encoding="utf-8"))


@app.command("show-config")
def show_config(
    config: Annotated[Path, typer.Option(exists=True, dir_okay=False)] = Path(
        "config/default.yaml"
    ),
    command: Annotated[
        str | None,
        typer.Option(help="Show merged run settings for scrape-live or parse-offline."),
    ] = None,
) -> None:
    """Display the effective YAML configuration in English."""
    if command is not None:
        if command not in {"scrape-live", "parse-offline"}:
            raise typer.BadParameter("command must be scrape-live or parse-offline")
        typer.echo(yaml.safe_dump(_effective_settings(command), sort_keys=True))
        return
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    typer.echo(yaml.safe_dump(data, sort_keys=True))
