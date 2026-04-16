import random
from collections import Counter

from typing import Dict, List, Iterator 


class _Balancer:
    def __init__(self):
        self.domains: List[str] = []
        self._dc_to_domain: Dict[int, str] = {}
    
    def update_domains_list(self, domains_list: List[str]) -> None:
        if Counter(self.domains) == Counter(domains_list):
            return
        
        self.domains = domains_list[:]

        self._dc_to_domain: Dict[int, str] = {
            dc_id: random.choice(self.domains)
            for dc_id in (1, 2, 3, 4, 5, 203)
        }

    def update_domain_for_dc(self, dc_id: int, domain: str) -> bool:
        if self._dc_to_domain.get(dc_id) == domain:
            return False
        
        self._dc_to_domain[dc_id] = domain
        return True

    def get_domains_for_dc(self, dc_id: int) -> Iterator[str]:
        current_domain = self._dc_to_domain.get(dc_id)
        yield current_domain

        shuffled_domains = self.domains[:]
        random.shuffle(shuffled_domains)

        for domain in shuffled_domains:
            if domain != current_domain:
                yield domain


balancer = _Balancer()
