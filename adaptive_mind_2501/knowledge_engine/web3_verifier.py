"""Local Web3 memory-tier verifier for Adaptive Mind Knowledge Engine.

Uses free public Polygon JSON-RPC endpoints via ``requests`` only (no cloud
backend, no web3.py). Grants ``PRO_MEMORY`` when the configured wallet holds
>= 1000 OMNI or has signed a local data-contribution hash; otherwise
``COMMUNITY``. All public APIs swallow network/config errors so ROS 2 nodes
never crash on offline / RPC failure.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import requests

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
TIER_PRO_MEMORY = 'PRO_MEMORY'
TIER_COMMUNITY = 'COMMUNITY'

OMNI_TOKEN_CONTRACT = '0x3daeff71F424859728dBB6bB690A19879E1a6714'
OMNI_PRO_THRESHOLD = 1000  # whole tokens (scaled by ERC-20 decimals)

DEFAULT_POLYGON_RPC_URLS: Sequence[str] = (
    'https://polygon-rpc.com',
    'https://rpc-mainnet.matic.quiknode.pro',
    'https://polygon.llamarpc.com',
    'https://1rpc.io/matic',
)

# ERC-20 selectors (first 4 bytes of keccak256)
_BALANCE_OF_SELECTOR = '70a08231'
_DECIMALS_SELECTOR = '313ce567'

_RPC_TIMEOUT_SEC = 4.0
_CONFIG_FILENAMES = ('brain_params.yaml', 'brain_params.yml')


class Web3Verifier:
    """Resolve Knowledge Engine memory tier from on-chain OMNI balance / local proof.

    Parameters
    ----------
    wallet_address:
        Explicit wallet override. If omitted, loaded from ``brain_params.yaml``.
    config_path:
        Path to ``brain_params.yaml``. Searched relative to CWD / package share
        when omitted.
    rpc_urls:
        Polygon JSON-RPC HTTP endpoints (tried in order).
    contribution_proof_path:
        Optional JSON proof of a signed local contribution hash.
    token_contract:
        OMNI ERC-20 contract on Polygon.
    pro_threshold:
        Minimum whole-token balance for ``PRO_MEMORY``.
    """

    def __init__(
        self,
        wallet_address: Optional[str] = None,
        config_path: Optional[Union[str, Path]] = None,
        rpc_urls: Optional[Sequence[str]] = None,
        contribution_proof_path: Optional[Union[str, Path]] = None,
        token_contract: str = OMNI_TOKEN_CONTRACT,
        pro_threshold: int = OMNI_PRO_THRESHOLD,
    ) -> None:
        self.token_contract = _normalize_address(token_contract) or OMNI_TOKEN_CONTRACT
        self.pro_threshold = int(pro_threshold)
        self.rpc_urls: List[str] = list(rpc_urls or DEFAULT_POLYGON_RPC_URLS)

        params = _load_brain_params(config_path)
        ros_params = _extract_ros_parameters(params)

        self.wallet_address = _normalize_address(
            wallet_address
            or ros_params.get('wallet_address')
            or os.environ.get('ADAPTIVE_MIND_WALLET', '')
        )

        proof = contribution_proof_path or ros_params.get('contribution_proof_path')
        self.contribution_proof_path: Optional[Path] = (
            Path(str(proof)).expanduser() if proof else None
        )

        cfg_rpcs = ros_params.get('polygon_rpc_urls')
        if isinstance(cfg_rpcs, list) and cfg_rpcs:
            self.rpc_urls = [str(u) for u in cfg_rpcs if u]

        cfg_threshold = ros_params.get('omni_pro_threshold')
        if cfg_threshold is not None:
            try:
                self.pro_threshold = int(cfg_threshold)
            except (TypeError, ValueError):
                pass

        cfg_contract = ros_params.get('omni_token_contract')
        if cfg_contract:
            normalized = _normalize_address(str(cfg_contract))
            if normalized:
                self.token_contract = normalized

        self._cached_tier: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Public API (never raises)
    # ------------------------------------------------------------------ #
    def get_tier(self, *, force_refresh: bool = False) -> str:
        """Return ``PRO_MEMORY`` or ``COMMUNITY``. Safe for ROS spin loops."""
        if self._cached_tier is not None and not force_refresh:
            return self._cached_tier

        tier = TIER_COMMUNITY
        try:
            if self._has_signed_contribution():
                tier = TIER_PRO_MEMORY
                logger.info('Web3Verifier: PRO_MEMORY via signed contribution proof')
            elif self._balance_grants_pro():
                tier = TIER_PRO_MEMORY
                logger.info('Web3Verifier: PRO_MEMORY via OMNI balance')
            else:
                logger.info('Web3Verifier: COMMUNITY tier')
        except Exception as exc:  # noqa: BLE001 — never crash ROS nodes
            logger.warning('Web3Verifier: falling back to COMMUNITY (%s)', exc)
            tier = TIER_COMMUNITY

        self._cached_tier = tier
        return tier

    def is_pro_memory(self, *, force_refresh: bool = False) -> bool:
        return self.get_tier(force_refresh=force_refresh) == TIER_PRO_MEMORY

    def can_export_extended_networkx(self, *, force_refresh: bool = False) -> bool:
        """True when extended NetworkX node-link JSON export is allowed."""
        return self.is_pro_memory(force_refresh=force_refresh)

    def get_omni_balance(self) -> Optional[float]:
        """Return whole-token OMNI balance, or ``None`` if unreachable."""
        if not self.wallet_address:
            return None
        try:
            raw = self._eth_call_uint(
                self.token_contract,
                _BALANCE_OF_SELECTOR + _address_arg(self.wallet_address),
            )
            if raw is None:
                return None
            decimals = self._token_decimals()
            return raw / float(10 ** decimals)
        except Exception as exc:  # noqa: BLE001
            logger.debug('Web3Verifier balance query failed: %s', exc)
            return None

    # ------------------------------------------------------------------ #
    # Tier checks
    # ------------------------------------------------------------------ #
    def _balance_grants_pro(self) -> bool:
        if not self.wallet_address:
            logger.debug('Web3Verifier: no wallet_address configured')
            return False
        balance = self.get_omni_balance()
        if balance is None:
            return False
        return balance >= float(self.pro_threshold)

    def _has_signed_contribution(self) -> bool:
        """Validate a local contribution proof signed by the configured wallet."""
        path = self.contribution_proof_path
        if path is None or not path.is_file():
            return False
        if not self.wallet_address:
            return False

        try:
            proof = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning('Web3Verifier: bad contribution proof %s: %s', path, exc)
            return False

        if not isinstance(proof, dict):
            return False

        proof_wallet = _normalize_address(
            proof.get('wallet') or proof.get('address') or ''
        )
        contribution_hash = str(
            proof.get('contribution_hash') or proof.get('hash') or ''
        ).strip()
        signature = str(proof.get('signature') or '').strip()

        if not proof_wallet or proof_wallet != self.wallet_address:
            return False
        if not contribution_hash or not signature:
            return False

        message = proof.get('message')
        if not message:
            message = f'AdaptiveMind2501 contribution:{contribution_hash}'

        return _verify_ethereum_personal_sign(
            message=str(message),
            signature=signature,
            expected_address=self.wallet_address,
        )

    def _token_decimals(self) -> int:
        raw = self._eth_call_uint(self.token_contract, _DECIMALS_SELECTOR)
        if raw is None:
            return 18
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 18
        if 0 <= value <= 36:
            return value
        return 18

    # ------------------------------------------------------------------ #
    # JSON-RPC helpers
    # ------------------------------------------------------------------ #
    def _eth_call_uint(self, to: str, data_hex: str) -> Optional[int]:
        result = self._eth_call(to, data_hex)
        if result is None:
            return None
        try:
            text = result.lower()
            if text.startswith('0x'):
                text = text[2:]
            if not text or text == '0' * len(text):
                return 0
            return int(text, 16)
        except (TypeError, ValueError):
            return None

    def _eth_call(self, to: str, data_hex: str) -> Optional[str]:
        payload = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'eth_call',
            'params': [
                {
                    'to': to if to.startswith('0x') else f'0x{to}',
                    'data': data_hex if data_hex.startswith('0x') else f'0x{data_hex}',
                },
                'latest',
            ],
        }
        last_error: Optional[BaseException] = None
        for url in self.rpc_urls:
            try:
                response = requests.post(
                    url,
                    json=payload,
                    timeout=_RPC_TIMEOUT_SEC,
                    headers={'Content-Type': 'application/json'},
                )
                response.raise_for_status()
                body = response.json()
                if isinstance(body, dict) and body.get('error'):
                    last_error = RuntimeError(str(body['error']))
                    continue
                result = body.get('result') if isinstance(body, dict) else None
                if isinstance(result, str) and result:
                    return result
                last_error = RuntimeError(f'empty eth_call result from {url}')
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.debug('Web3Verifier RPC %s failed: %s', url, exc)
                continue
        if last_error is not None:
            logger.warning(
                'Web3Verifier: all Polygon RPC endpoints failed (%s)', last_error
            )
        return None


# --------------------------------------------------------------------------- #
# Module-level convenience (ROS-safe)
# --------------------------------------------------------------------------- #
_default_verifier: Optional[Web3Verifier] = None


def get_verifier(**kwargs: Any) -> Web3Verifier:
    """Return a process-wide default :class:`Web3Verifier` (lazy singleton)."""
    global _default_verifier
    if _default_verifier is None or kwargs:
        _default_verifier = Web3Verifier(**kwargs)
    return _default_verifier


def get_memory_tier(**kwargs: Any) -> str:
    """Resolve memory tier; always returns ``PRO_MEMORY`` or ``COMMUNITY``."""
    try:
        return get_verifier(**kwargs).get_tier()
    except Exception as exc:  # noqa: BLE001
        logger.warning('get_memory_tier fallback to COMMUNITY: %s', exc)
        return TIER_COMMUNITY


def can_export_extended_networkx(**kwargs: Any) -> bool:
    try:
        return get_verifier(**kwargs).can_export_extended_networkx()
    except Exception as exc:  # noqa: BLE001
        logger.warning('can_export_extended_networkx fallback False: %s', exc)
        return False


# --------------------------------------------------------------------------- #
# Config / crypto helpers
# --------------------------------------------------------------------------- #
def _normalize_address(value: Optional[str]) -> Optional[str]:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.lower() in {'', 'null', 'none', '0x'}:
        return None
    if not text.startswith('0x'):
        text = '0x' + text
    if len(text) != 42:
        return None
    try:
        int(text[2:], 16)
    except ValueError:
        return None
    return text.lower()


def _address_arg(address: Optional[str]) -> str:
    if not address:
        return '0' * 64
    return address.lower().replace('0x', '').rjust(64, '0')


def _extract_ros_parameters(params: Dict[str, Any]) -> Dict[str, Any]:
    if not params:
        return {}
    # ROS 2 wildcard param file: /**: { ros__parameters: { ... } }
    if '/**' in params and isinstance(params['/**'], dict):
        nested = params['/**'].get('ros__parameters')
        if isinstance(nested, dict):
            return nested
    if 'ros__parameters' in params and isinstance(params['ros__parameters'], dict):
        return params['ros__parameters']
    return params


def _load_brain_params(config_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    path = _resolve_config_path(config_path)
    if path is None or not path.is_file():
        logger.debug('Web3Verifier: brain_params.yaml not found')
        return {}
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        logger.warning('Web3Verifier: cannot read %s: %s', path, exc)
        return {}

    try:
        import yaml  # type: ignore
    except ImportError:
        logger.warning(
            'Web3Verifier: PyYAML not installed; cannot parse %s', path.name
        )
        return {}

    try:
        data = yaml.safe_load(text) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning('Web3Verifier: YAML parse failed for %s: %s', path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_config_path(
    config_path: Optional[Union[str, Path]] = None,
) -> Optional[Path]:
    if config_path is not None:
        candidate = Path(config_path).expanduser()
        return candidate

    env = os.environ.get('ADAPTIVE_MIND_BRAIN_PARAMS')
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p

    here = Path(__file__).resolve()
    search_roots: List[Path] = [
        Path.cwd() / 'config',
        Path.cwd(),
        here.parents[2] / 'config',  # repo root .../Adaptive_Mind_2501/config
    ]
    try:
        from ament_index_python.packages import get_package_share_directory

        share = Path(get_package_share_directory('adaptive_mind_2501')) / 'config'
        search_roots.insert(0, share)
    except Exception:  # noqa: BLE001
        pass

    for root in search_roots:
        if root.is_file() and root.name in _CONFIG_FILENAMES:
            return root
        for name in _CONFIG_FILENAMES:
            candidate = root / name
            if candidate.is_file():
                return candidate
    return None


def _verify_ethereum_personal_sign(
    message: str,
    signature: str,
    expected_address: str,
) -> bool:
    """Recover signer from EIP-191 personal_sign; optional ``eth_account``."""
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except ImportError:
        logger.warning(
            'Web3Verifier: eth_account not installed; '
            'cannot verify contribution signature (balance check still works)'
        )
        return False

    try:
        signable = encode_defunct(text=message)
        recovered = Account.recover_message(signable, signature=signature)
        return recovered.lower() == expected_address.lower()
    except Exception as exc:  # noqa: BLE001
        logger.warning('Web3Verifier: signature verification failed: %s', exc)
        return False
