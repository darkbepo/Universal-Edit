import os
import time

from umc.tools import libreoffice


def test_kept_profile_is_claimed_by_one_run_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setattr(libreoffice, "kept_profile_dir", lambda: tmp_path / "libreoffice-profile")
    with libreoffice._KeptProfile() as first:
        assert first == tmp_path / "libreoffice-profile"
        with libreoffice._KeptProfile() as second:  # e.g. another app window
            assert second is None  # → that run uses a temporary profile
    with libreoffice._KeptProfile() as again:  # released after use
        assert again == first


def test_a_claim_left_by_a_crash_is_taken_over(tmp_path, monkeypatch):
    monkeypatch.setattr(libreoffice, "kept_profile_dir", lambda: tmp_path / "libreoffice-profile")
    claim = tmp_path / "libreoffice-profile.in-use"
    claim.write_text("")
    old = time.time() - libreoffice._KeptProfile.STALE_AFTER - 60
    os.utime(claim, (old, old))
    with libreoffice._KeptProfile() as kept:
        assert kept is not None
    assert not claim.exists()
