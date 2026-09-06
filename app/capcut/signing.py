"""Minimal local request-signing helpers extracted for this project."""

import base64
import binascii
import datetime as dt
import hashlib
import hmac
import json
import secrets
import time
import uuid
from typing import Any, Optional, Tuple, Union
from urllib.parse import parse_qsl, quote, urlsplit

VOD_REGION = "sdwdmwlll"
VOD_SERVICE = "vod"
TTS_SIGN_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAmTd34Lw4b7IuldSXh/zY
CMla+ITdGG5TeWz6ad+OySd4r+IrY45AoqrYUxhQ2dl+7z+i7r/5vEa8rr39BYfB
8AGMQLmZA8HmgpWBsqrn/V6daUALkKnkLb70Fn32CJigIuGXAYqxUdGuI340aC+0
v5Es3puJsHyzf01/AelE4Cdc6bZhQrASJLBh8R3BQToYClmDVSDUQk28o8sl/guA
Z4n303Vj+6Siv1HayPCdV6kpVVnMBAG4+umUbwGmn132N3fgpzLarFF3XyWmS1zh
D/J07iM/rP8GDO9IskHNHd2phrO0G6KzrcFAnTBHjVv+hCBEfzN/no3FNA9AuC36
mwIDAQAB
-----END PUBLIC KEY-----"""

def compact_json(obj: Any) -> str: return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
def make_x_ss_stub(body_text: str) -> str: return hashlib.md5(body_text.encode()).hexdigest()
def make_trace_id() -> str:
    seed = uuid.uuid4().hex
    return f"00-{seed}-{seed[:16]}-01"
def escape_xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")

def _der_len(data: bytes, pos: int) -> Tuple[int, int]:
    first = data[pos]; pos += 1
    if first < 0x80: return first, pos
    n = first & 0x7F
    return int.from_bytes(data[pos:pos+n], "big"), pos+n
def _der_value(data: bytes, pos: int, tag: int) -> Tuple[bytes, int]:
    if data[pos] != tag: raise ValueError("invalid DER")
    length, pos = _der_len(data, pos + 1)
    return data[pos:pos+length], pos+length
def _der_int(data: bytes, pos: int) -> Tuple[int, int]:
    raw, pos = _der_value(data, pos, 2)
    return int.from_bytes(raw.lstrip(b"\0"), "big"), pos
def rsa_public_numbers_from_pem(pem: str) -> Tuple[int, int]:
    raw = base64.b64decode("".join(x for x in pem.splitlines() if not x.startswith("---")))
    outer, _ = _der_value(raw, 0, 0x30)
    alg, pos = _der_value(outer, 0, 0x30)
    bits, pos = _der_value(outer, pos, 3)
    seq, _ = _der_value(bits[1:], 0, 0x30)
    modulus, pos = _der_int(seq, 0)
    exponent, _ = _der_int(seq, pos)
    return modulus, exponent
def rsa_encrypt_pkcs1v15(message: Union[str, bytes], pem: str = TTS_SIGN_PUBLIC_KEY_PEM) -> str:
    modulus, exponent = rsa_public_numbers_from_pem(pem)
    size = (modulus.bit_length() + 7) // 8
    msg = message.encode() if isinstance(message, str) else bytes(message)
    ps = bytearray()
    while len(ps) < size - len(msg) - 3:
        ps.extend(x for x in secrets.token_bytes(size) if x)
    encoded = b"\0\2" + bytes(ps[:size-len(msg)-3]) + b"\0" + msg
    return base64.b64encode(pow(int.from_bytes(encoded, "big"), exponent, modulus).to_bytes(size, "big")).decode()
def make_tts_payload_sign(ssml: str, extra_info: Optional[str], device_id: str, app_id: str) -> str:
    value = f"appid:{app_id}&did:{device_id}&creditDisable:false&ssml:{hashlib.md5(ssml.encode()).hexdigest()}"
    if extra_info is not None: value += f"&extraInfo:{extra_info}"
    return rsa_encrypt_pkcs1v15(value)
def make_sign_header(url: str, appvr: str, device_time: str, tdid: str, pf: str = "3") -> str:
    path = url.split("?", 1)[0]
    return hashlib.md5(f"9e2c|{path[-7:]}|{pf}|{appvr}|{device_time}|{tdid}|11ac".encode()).hexdigest()
def hmac_sha256(key, msg) -> bytes:
    return hmac.new(key.encode() if isinstance(key, str) else key, msg.encode() if isinstance(msg, str) else msg, hashlib.sha256).digest()
def aws4_signing_key(secret_access_key: str, date_stamp: str, region: str = VOD_REGION, service: str = VOD_SERVICE) -> bytes:
    return hmac_sha256(hmac_sha256(hmac_sha256(hmac_sha256("AWS4" + secret_access_key, date_stamp), region), service), "aws4_request")
def canonical_query(url: str) -> str:
    return "&".join(quote(str(k), safe="-_.~") + "=" + quote(str(v), safe="-_.~") for k, v in sorted(parse_qsl(urlsplit(url).query, keep_blank_values=True)))
def sha256_hex(data) -> str: return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()
def aws4_authorization(method, url, body, access_key_id, secret_access_key, session_token, amz_date) -> str:
    scope = f"{amz_date[:8]}/{VOD_REGION}/{VOD_SERVICE}/aws4_request"
    signed = "x-amz-date;x-amz-security-token"
    headers = f"x-amz-date:{amz_date}\nx-amz-security-token:{session_token}\n"
    request = "\n".join([method, urlsplit(url).path, canonical_query(url), headers, signed, sha256_hex(body)])
    string = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, sha256_hex(request)])
    sig = hmac.new(aws4_signing_key(secret_access_key, amz_date[:8]), string.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={access_key_id}/{scope}, SignedHeaders={signed}, Signature={sig}"
def utc_now_for_vod() -> Tuple[str, str]:
    now = dt.datetime.now(dt.timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%a, %d %b %Y %H:%M:%S GMT")
