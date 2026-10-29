"""Bounded stress of the real engine: 100/500/1000 attempts, max 50 in flight.

The runner's PostgreSQL pool is intentionally bounded. This does NOT prove
1,000 simultaneously active users or production capacity.
"""
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import resource
import time
from uuid import uuid4

from fastapi import HTTPException
import pytest

from tests.integration.test_booking import booking_db,phone,start_at
from app.booking_service import book_appointment,confirm_hold

pytestmark=pytest.mark.integration


@pytest.mark.parametrize('operations',[100,500,1000])
@pytest.mark.asyncio
async def test_bounded_contention_has_one_winner_and_preserves_slot(booking_db,operations,monkeypatch):
    if os.getenv('ORION_APPOINTMENT_LOAD')!='1':pytest.skip('Explicit isolated load runner only')
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_ENABLED','0')
    d=booking_db;limit=asyncio.Semaphore(50);latencies=[];outcomes=[]
    async def attempt(index):
        async with limit:
            started=time.perf_counter()
            async with d['factory']() as db:
                try:
                    hold=await book_appointment(db,d['a'],d['pro'],[d['cut']],phone(10000+index),
                        'Carga sintética',start_at(d['day'],10),'load-'+str(uuid4()))
                    await confirm_hold(db,d['a'],hold['id']);outcomes.append('confirmed')
                except HTTPException as exc:outcomes.append(str(exc.status_code))
            latencies.append((time.perf_counter()-started)*1000)
    started=time.perf_counter();cpu=time.process_time()
    await asyncio.gather(*(attempt(i) for i in range(operations)))
    elapsed=time.perf_counter()-started
    counts=Counter(outcomes)
    assert counts=={'confirmed':1,'409':operations-1},counts
    ordered=sorted(latencies)
    result={'attempts':operations,'max_in_flight':50,'elapsed_seconds':round(elapsed,3),
        'throughput_per_second':round(operations/elapsed,2),'p50_ms':round(ordered[int(len(ordered)*.5)],2),
        'p95_ms':round(ordered[int(len(ordered)*.95)],2),'p99_ms':round(ordered[min(len(ordered)-1,int(len(ordered)*.99))],2),
        'outcomes':dict(counts),'python_cpu_seconds':round(time.process_time()-cpu,3),
        'python_peak_rss_kb':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'scope':'isolated PostgreSQL engine only; no WhatsApp delivery or production capacity claim'}
    directory=Path('test-results/appointment-communications');directory.mkdir(parents=True,exist_ok=True)
    (directory/f'load-{operations}.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))
