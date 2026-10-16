"""YOYO Options Pro: incremental daily research feed. No trading execution."""
import json, os, time, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

SYMBOLS = 'SPY QQQ IWM DIA NVDA AAPL MSFT AMD GOOGL TSLA AMZN META PLTR NFLX AVGO MU INTC TSM JPM BAC GS COIN MSTR HOOD SOFI'.split()
AV = os.getenv('ALPHA_VANTAGE_API_KEY','').strip()
TRADIER = os.getenv('TRADIER_TOKEN','').strip()
TRADIER_BASE = os.getenv('TRADIER_BASE_URL','https://api.tradier.com').rstrip('/')
BATCH = max(1,min(25,int(os.getenv('SYMBOLS_PER_RUN','5'))))
DELAY = max(0,float(os.getenv('REQUEST_DELAY_SECONDS','15')))
CACHE = Path('stock_signals.json')

def read_json(path, default):
    try: return json.loads(Path(path).read_text())
    except (OSError, ValueError): return default

def fetch(url, headers=None):
    req = urllib.request.Request(url,headers=headers or {'User-Agent':'YOYO-Options-Pro/1.0'})
    with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)

def stock(symbol):
    query = urllib.parse.urlencode(dict(function='TIME_SERIES_DAILY',symbol=symbol,outputsize='compact',apikey=AV))
    raw=fetch('https://www.alphavantage.co/query?'+query)
    series=raw.get('Time Series (Daily)')
    if not isinstance(series,dict): raise ValueError('Alpha Vantage unavailable or rate-limited: '+str(list(raw)))
    dates=sorted(series)
    if len(dates)<22: raise ValueError('Need 22 completed candles')
    candles=[series[d] for d in dates]
    close=[float(c['4. close']) for c in candles]
    high=[float(c['2. high']) for c in candles]
    low=[float(c['3. low']) for c in candles]
    vol=[float(c['5. volume']) for c in candles]
    def bb(a,n):
        mean=sum(a)/len(a)
        std=(sum((x-mean)**2 for x in a)/len(a))**.5
        return {'upper':round(mean+n*std,4),'middle':round(mean,4),'lower':round(mean-n*std,4)}
    current2=bb(close[-20:],2); current3=bb(close[-20:],3)
    prev2=bb(close[-21:-1],2)
    vr=vol[-1]/(sum(vol[-21:-1])/20) if sum(vol[-21:-1]) else 0
    direction='WAIT'; reason='No confirmed experimental setup'
    if close[-2]<prev2['lower'] and close[-1]>current2['lower'] and close[-1]>close[-2] and vr>=1.2:
        direction='CALL';reason='Experimental lower-band reversal and volume confirmation'
    elif close[-2]>prev2['upper'] and close[-1]<current2['upper'] and close[-1]<close[-2] and vr>=1.2:
        direction='PUT';reason='Experimental upper-band rejection and volume confirmation'
    return dict(symbol=symbol,timeframe='1D',date=dates[-1],updated_at_utc=datetime.now(timezone.utc).isoformat(),close=round(close[-1],4),volume_ratio=round(vr,2),support=round(min(low[-20:]),4),resistance=round(max(high[-20:]),4),bb20_2=current2,bb20_3=current3,signal=direction,reason=reason,status='EXPERIMENTAL',note='Research only; not an options trade recommendation.')

def options(symbol):
    """Fetch actual Tradier option chains for three DTE windows; never synthesize quotes."""
    if not TRADIER:
        return {'status':'UNAVAILABLE','reason':'TRADIER_TOKEN not configured'}
    headers={'Authorization':'Bearer '+TRADIER,'Accept':'application/json'}
    base=TRADIER_BASE+'/v1/markets/options/'
    raw=fetch(base+'expirations?'+urllib.parse.urlencode({'symbol':symbol}),headers)
    exp=raw.get('expirations') or {}
    dates=exp.get('date',[]) if isinstance(exp,dict) else []
    if isinstance(dates,str): dates=[dates]
    today=datetime.now(timezone.utc).date()
    windows={'14-21':(14,21),'30-45':(30,45),'60-90':(60,90)}
    by_dte={}
    for label,(start,end) in windows.items():
        candidates=[]
        for value in dates:
            try:
                days=(datetime.strptime(value,'%Y-%m-%d').date()-today).days
                if start<=days<=end: candidates.append((days,value))
            except (TypeError,ValueError): pass
        if not candidates:
            by_dte[label]={'status':'UNAVAILABLE','reason':'No listed expiration in selected DTE window'}
            continue
        _,expiration=min(candidates)
        try:
            raw=fetch(base+'chains?'+urllib.parse.urlencode({'symbol':symbol,'expiration':expiration,'greeks':'false'}),headers)
            chain=(raw.get('options') or {}).get('option',[])
            if isinstance(chain,dict): chain=[chain]
            items=[{'type':x.get('option_type'),'strike':x.get('strike'),'expiration':expiration,
                    'bid':x.get('bid'),'ask':x.get('ask'),'volume':x.get('volume'),
                    'open_interest':x.get('open_interest'),'symbol':x.get('symbol')}
                   for x in chain if isinstance(x,dict)]
            by_dte[label]={'status':'AVAILABLE' if items else 'UNAVAILABLE',
                           'expiration':expiration,'contracts':items,'source':'Tradier',
                           'updated_at_utc':datetime.now(timezone.utc).isoformat()}
        except Exception as exc:
            by_dte[label]={'status':'UNAVAILABLE','reason':str(exc)[:160]}
    return {'status':'AVAILABLE','by_dte':by_dte,'source':'Tradier'}

def main():
    if not AV: raise SystemExit('ALPHA_VANTAGE_API_KEY is missing')
    existing=read_json(CACHE,{})
    if not isinstance(existing,dict): existing={}
    # Rotate so 25 symbols are covered over multiple runs. Limit according to API plan.
    state=read_json('.yoyo_cursor.json',{'cursor':0})
    cursor=int(state.get('cursor',0))%len(SYMBOLS)
    subset=[SYMBOLS[(cursor+i)%len(SYMBOLS)] for i in range(BATCH)]
    updated=0
    for i,symbol in enumerate(subset):
        try:
            record=stock(symbol)
            try: record['options']=options(symbol)
            except Exception as exc: record['options']={'status':'UNAVAILABLE','reason':str(exc)[:160]}
            existing[symbol]=record;updated+=1
            print(symbol,record['date'],record['signal'])
        except Exception as exc: print('ERROR',symbol,str(exc)[:160])
        if i<len(subset)-1: time.sleep(DELAY)
    # Never publish a record for a symbol that has never been fetched.
    CACHE.write_text(json.dumps(existing,indent=2)+'\n')
    if 'SPY' in existing: Path('stock_signal.json').write_text(json.dumps(existing['SPY'],indent=2)+'\n')
    Path('.yoyo_cursor.json').write_text(json.dumps({'cursor':(cursor+BATCH)%len(SYMBOLS)},indent=2)+'\n')
    print('Updated',updated,'of',len(subset),'attempted; cached symbols:',len(existing))
    if updated==0: raise SystemExit('No market data fetched; existing cache retained')

if __name__=='__main__': main()
