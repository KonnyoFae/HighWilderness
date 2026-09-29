"""Small AV4 observation/EW sample, not a maximum-fleet benchmark."""
from pathlib import Path
from statistics import median
from time import perf_counter
import json
import platform
import sys
from tools.test_aviation_ew import AviationEWTests


def measure(steps=600):
    AviationEWTests.setUpClass();fixture=AviationEWTests();rows=[]
    for enabled in (False,True):
        b,key=fixture.flying();fixture.command(b,'emissions',[key],emissions=dict(radar=True,jammer=enabled))
        for _ in range(30):b.step()
        timings=[];maximum=dict(aircraft=0,aircraft_sensors=0,areas=0,projectiles=0,queued_jobs=0)
        for _ in range(steps):
            start=perf_counter();b.step();timings.append((perf_counter()-start)*1000)
            counts=dict(aircraft=len(b.aviation.flights),aircraft_sensors=2*len(b.aviation.flights),areas=len(fixture.environment(b).areas),
                projectiles=len(b.projectiles),queued_jobs=sum(len(i._value['aviation']['queue']) for i in b.inventory.inventories))
            maximum={k:max(maximum[k],v) for k,v in counts.items()}
        rows.append(dict(jammer=enabled,ships=2,steps=steps,simulated_seconds=steps/60,maximum=maximum,
            step_median_ms=median(timings),step_p95_ms=sorted(timings)[int(.95*(len(timings)-1))],step_total_ms=sum(timings)))
    return dict(scope='Two carriers, one airborne E1, both aircraft sensors active; 30 warmup steps. Simulation step only, excludes publication/rendering. No maximum-load or balance claim.',python=platform.python_version(),samples=rows)


if __name__=='__main__':
    result=measure();path=Path(sys.argv[1]);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
