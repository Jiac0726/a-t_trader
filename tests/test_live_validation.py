from datetime import date
import pandas as pd
from validation.live import run_live_validation


def bars(n=20, freq="D"):
    dt = pd.date_range("2026-01-01", periods=n, freq=freq)
    return pd.DataFrame({"datetime":dt,"open":range(1,n+1),"high":range(2,n+2),"low":[max(0,x-1) for x in range(1,n+1)],"close":range(1,n+1),"volume":[100]*n,"amount":[1000]*n})

class Market:
    def stock_list(self):
        rows=[]
        for i in range(3100): rows.append({"code":f"{i:06d}","name":f"S{i}","market":"SH" if i%2 else "SZ"})
        return pd.DataFrame(rows)
    def history(self, code, start, end, interval="1d", adjust="qfq"):
        return bars(30 if interval=="1d" else 50, "D" if interval=="1d" else "5min")
class Bench:
    def history(self,*args,**kwargs): return bars(30)
class Master:
    def snapshot(self, day):
        return pd.DataFrame({"code":[f"{i:06d}" for i in range(2600)],"name":["x"]*2600,"as_of":[pd.Timestamp(day)]*2600})

def test_live_validation_happy_path():
    report=run_live_validation(Market(),Bench(),security_master_provider=Master(),persistence_probe=lambda:("ok",{}),end=date(2026,2,1))
    assert report.ok
    assert all(x.status=="PASS" for x in report.checks)

def test_live_validation_flags_small_universe():
    class Small(Market):
        def stock_list(self): return super().stock_list().head(10)
    report=run_live_validation(Small(),Bench(),end=date(2026,2,1))
    assert not report.ok
    assert any(x.name=="market_universe" and x.status=="FAIL" for x in report.checks)
