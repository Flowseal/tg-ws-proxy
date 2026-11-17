import random
import time
from collections import Counter

from typing import Dict, List, Iterator 


class _Balancer:
    """Picks a CF proxy domain for a DC and remembers which ones are broken.

    During a mass block every client connection used to retry the whole
    domain pool: a blocked domain answers with a timeout, so one connect could
    spend minutes cycling through domains that are all gone. A domain that
    just failed is therefore skipped for a cooldown; the pause doubles on
    repeated failures and is cleared by the first success.
    """

    FAILURE_COOLDOWN_INITIAL = 30.0
    FAILURE_COOLDOWN_MAX = 120.0

    def __init__(self):
        self.domains: List[str] = []
        self._dc_to_domain: Dict[int, str] = {}
        self._failures: Dict[str, int] = {}
        self._cooldown_until: Dict[str, float] = {}

    def update_domains_list(self, domains_list: List[str]) -> None:
        if Counter(self.domains) == Counter(domains_list):
            return
        
        self.domains = domains_list[:]

        if not self.domains:
            self._dc_to_domain = {}
        else:
            self._dc_to_domain = {
                dc_id: random.choice(self.domains)
                for dc_id in (1, 2, 3, 4, 5, 203)
            }

        alive = set(self.domains)
        self._failures = {
            domain: count for domain, count in self._failures.items()
            if domain in alive
        }
        self._cooldown_until = {
            domain: until for domain, until in self._cooldown_until.items()
            if domain in alive
        }

    def reset_health(self) -> None:
        self._failures.clear()
        self._cooldown_until.clear()

    def update_domain_for_dc(self, dc_id: int, domain: str) -> bool:
        if self._dc_to_domain.get(dc_id) == domain:
            return False
        
        self._dc_to_domain[dc_id] = domain
        return True

    def report_failure(self, domain: str) -> float:
        """Pause the domain and return the pause length in seconds."""
        failures = self._failures.get(domain, 0) + 1
        self._failures[domain] = failures
        delay = min(
            self.FAILURE_COOLDOWN_INITIAL * 2 ** min(failures - 1, 8),
            self.FAILURE_COOLDOWN_MAX,
        )
        self._cooldown_until[domain] = time.monotonic() + delay
        return delay

    def report_success(self, domain: str) -> None:
        self._failures.pop(domain, None)
        self._cooldown_until.pop(domain, None)

    def get_domains_for_dc(self, dc_id: int) -> Iterator[str]:
        now = time.monotonic()
        current_domain = self._dc_to_domain.get(dc_id)

        healthy_domains = [
            domain for domain in self.domains
            if self._cooldown_until.get(domain, 0.0) <= now
        ]
        if not healthy_domains:
            # Everything is cooling down. Fail fast instead of hammering the
            # same dead domains again; the first connect after the cooldown
            # probes them once more.
            return

        if current_domain in healthy_domains:
            healthy_domains.remove(current_domain)
            yield current_domain

        random.shuffle(healthy_domains)

        for domain in healthy_domains:
            yield domain


balancer = _Balancer()
