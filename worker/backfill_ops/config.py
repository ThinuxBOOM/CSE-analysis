"""
The server configuration HB-6 reads, from the environment only (systemd EnvironmentFile=/etc/cse/cse.env,
/etc/cse/capture.env and /etc/cse/backfill.env; ops/config/*.example are the templates). Nothing machine-specific and
no secret lives in the repository: the contact e-mail is P2's CSE_CAPTURE_CONTACT_EMAIL, the temporary root is HB-4's
CSE_BACKFILL_TEMP_ROOT, the database is P1's (peer authentication on the local socket).
"""
import os
from dataclasses import dataclass
from typing import Optional

from ..backfill_documents import TEMP_ROOT_ENV
from ..ops import settings as ops_settings
from . import DB_DISK_ENV, REPORT_DIR_ENV


@dataclass(frozen=True)
class OpsConfig:
    settings: ops_settings.Settings
    env: dict
    report_dir: Optional[str]
    temp_root: Optional[str]
    db_disk_path: Optional[str]

    def connect(self, user=None):
        return ops_settings.connect(self.settings, user=user)


def load(env=None) -> OpsConfig:
    env = dict(os.environ if env is None else env)
    return OpsConfig(settings=ops_settings.load(env), env=env, report_dir=env.get(REPORT_DIR_ENV) or None,
                     temp_root=env.get(TEMP_ROOT_ENV) or None, db_disk_path=env.get(DB_DISK_ENV) or None)
