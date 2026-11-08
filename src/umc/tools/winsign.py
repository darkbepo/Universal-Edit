"""Windows Authenticode verification via the Win32 API (no PowerShell needed).

* :func:`is_trusted` — ``WinVerifyTrust`` with the standard Authenticode policy: the file must
  be signed, untampered, and chain to a trusted root.
* :func:`signer_name` — the display name of the certificate that signed the file
  (``CryptQueryObject`` → ``CMSG_SIGNER_INFO`` → ``CertGetNameStringW``).

Only importable/usable on Windows; callers guard with ``sys.platform``.
"""

from __future__ import annotations

import ctypes
import uuid
from ctypes import POINTER, Structure, byref, c_void_p, sizeof
from ctypes import wintypes as wt


class GUID(Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_str(cls, text: str) -> GUID:
        u = uuid.UUID(text)
        g = cls()
        g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        g.Data4[:] = list(u.bytes[8:])
        return g


WINTRUST_ACTION_GENERIC_VERIFY_V2 = "00AAC56B-CD44-11d0-8CC2-00C04FC295EE"


class WINTRUST_FILE_INFO(Structure):  # noqa: N801 (Win32 name)
    _fields_ = [
        ("cbStruct", wt.DWORD),
        ("pcwszFilePath", wt.LPCWSTR),
        ("hFile", wt.HANDLE),
        ("pgKnownSubject", c_void_p),
    ]


class WINTRUST_DATA(Structure):  # noqa: N801
    _fields_ = [
        ("cbStruct", wt.DWORD),
        ("pPolicyCallbackData", c_void_p),
        ("pSIPClientData", c_void_p),
        ("dwUIChoice", wt.DWORD),
        ("fdwRevocationChecks", wt.DWORD),
        ("dwUnionChoice", wt.DWORD),
        ("pFile", POINTER(WINTRUST_FILE_INFO)),
        ("dwStateAction", wt.DWORD),
        ("hWVTStateData", wt.HANDLE),
        ("pwszURLReference", wt.LPCWSTR),
        ("dwProvFlags", wt.DWORD),
        ("dwUIContext", wt.DWORD),
        ("pSignatureSettings", c_void_p),
    ]


WTD_UI_NONE = 2
WTD_REVOKE_NONE = 0
WTD_CHOICE_FILE = 1
WTD_STATEACTION_VERIFY = 1
WTD_STATEACTION_CLOSE = 2


def is_trusted(path: str) -> tuple[bool, int]:
    """Return ``(trusted, hresult)`` for the file's embedded Authenticode signature."""
    wintrust = ctypes.WinDLL("wintrust")
    file_info = WINTRUST_FILE_INFO(sizeof(WINTRUST_FILE_INFO), path, None, None)
    data = WINTRUST_DATA()
    data.cbStruct = sizeof(WINTRUST_DATA)
    data.dwUIChoice = WTD_UI_NONE
    data.fdwRevocationChecks = WTD_REVOKE_NONE
    data.dwUnionChoice = WTD_CHOICE_FILE
    data.pFile = ctypes.pointer(file_info)
    data.dwStateAction = WTD_STATEACTION_VERIFY
    action = GUID.from_str(WINTRUST_ACTION_GENERIC_VERIFY_V2)
    wintrust.WinVerifyTrust.restype = ctypes.c_long
    wintrust.WinVerifyTrust.argtypes = [wt.HWND, POINTER(GUID), c_void_p]
    hr = wintrust.WinVerifyTrust(None, byref(action), byref(data))
    data.dwStateAction = WTD_STATEACTION_CLOSE
    wintrust.WinVerifyTrust(None, byref(action), byref(data))
    return hr == 0, hr & 0xFFFFFFFF


class CRYPT_BLOB(Structure):  # noqa: N801 (CRYPT_INTEGER_BLOB / CERT_NAME_BLOB)
    _fields_ = [("cbData", wt.DWORD), ("pbData", c_void_p)]


class CRYPT_ALGORITHM_IDENTIFIER(Structure):  # noqa: N801
    _fields_ = [("pszObjId", wt.LPSTR), ("Parameters", CRYPT_BLOB)]


class CMSG_SIGNER_INFO(Structure):  # noqa: N801 (leading fields only — that's all we read)
    _fields_ = [("dwVersion", wt.DWORD), ("Issuer", CRYPT_BLOB), ("SerialNumber", CRYPT_BLOB)]


class CERT_INFO(Structure):  # noqa: N801 (leading fields used by CERT_FIND_SUBJECT_CERT)
    _fields_ = [
        ("dwVersion", wt.DWORD),
        ("SerialNumber", CRYPT_BLOB),
        ("SignatureAlgorithm", CRYPT_ALGORITHM_IDENTIFIER),
        ("Issuer", CRYPT_BLOB),
        ("NotBefore", wt.FILETIME),
        ("NotAfter", wt.FILETIME),
        ("Subject", CRYPT_BLOB),
        ("SubjectPublicKeyInfo", ctypes.c_byte * 64),  # padding; not read
    ]


CERT_QUERY_OBJECT_FILE = 1
CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED = 1 << 10
CERT_QUERY_FORMAT_FLAG_BINARY = 1 << 1
CMSG_SIGNER_INFO_PARAM = 6
ENCODING = 0x00000001 | 0x00010000  # X509_ASN_ENCODING | PKCS_7_ASN_ENCODING
CERT_FIND_SUBJECT_CERT = 11 << 16
CERT_NAME_SIMPLE_DISPLAY_TYPE = 4


def signer_name(path: str) -> str:
    """Display name of the signing certificate, or ``""`` if it can't be read."""
    crypt32 = ctypes.WinDLL("crypt32")
    crypt32.CryptQueryObject.argtypes = [
        wt.DWORD,
        c_void_p,
        wt.DWORD,
        wt.DWORD,
        wt.DWORD,
        POINTER(wt.DWORD),
        POINTER(wt.DWORD),
        POINTER(wt.DWORD),
        POINTER(wt.HANDLE),
        POINTER(wt.HANDLE),
        c_void_p,
    ]
    crypt32.CryptMsgGetParam.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, c_void_p, POINTER(wt.DWORD)]
    crypt32.CertFindCertificateInStore.restype = c_void_p
    crypt32.CertFindCertificateInStore.argtypes = [
        wt.HANDLE,
        wt.DWORD,
        wt.DWORD,
        wt.DWORD,
        c_void_p,
        c_void_p,
    ]
    crypt32.CertGetNameStringW.argtypes = [
        c_void_p,
        wt.DWORD,
        wt.DWORD,
        c_void_p,
        wt.LPWSTR,
        wt.DWORD,
    ]
    crypt32.CertFreeCertificateContext.argtypes = [c_void_p]
    crypt32.CertCloseStore.argtypes = [wt.HANDLE, wt.DWORD]
    crypt32.CryptMsgClose.argtypes = [wt.HANDLE]

    enc, ctype, ftype = wt.DWORD(), wt.DWORD(), wt.DWORD()
    store, msg = wt.HANDLE(), wt.HANDLE()
    ok = crypt32.CryptQueryObject(
        CERT_QUERY_OBJECT_FILE,
        ctypes.c_wchar_p(path),
        CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED,
        CERT_QUERY_FORMAT_FLAG_BINARY,
        0,
        byref(enc),
        byref(ctype),
        byref(ftype),
        byref(store),
        byref(msg),
        None,
    )
    if not ok:
        return ""
    try:
        size = wt.DWORD()
        if not crypt32.CryptMsgGetParam(msg, CMSG_SIGNER_INFO_PARAM, 0, None, byref(size)):
            return ""
        buf = ctypes.create_string_buffer(size.value)
        if not crypt32.CryptMsgGetParam(msg, CMSG_SIGNER_INFO_PARAM, 0, buf, byref(size)):
            return ""
        signer = CMSG_SIGNER_INFO.from_buffer(buf)
        info = CERT_INFO()
        info.Issuer = signer.Issuer
        info.SerialNumber = signer.SerialNumber
        cert = crypt32.CertFindCertificateInStore(
            store, ENCODING, 0, CERT_FIND_SUBJECT_CERT, byref(info), None
        )
        if not cert:
            return ""
        try:
            name = ctypes.create_unicode_buffer(512)
            crypt32.CertGetNameStringW(cert, CERT_NAME_SIMPLE_DISPLAY_TYPE, 0, None, name, 512)
            return name.value
        finally:
            crypt32.CertFreeCertificateContext(cert)
    finally:
        crypt32.CertCloseStore(store, 0)
        crypt32.CryptMsgClose(msg)
