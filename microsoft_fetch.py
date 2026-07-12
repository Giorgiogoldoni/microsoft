#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAPTOR Microsoft — Data Fetch
Scarica dati Microsoft (NASDAQ) (25 anni, MSFT) e 3LMS.MI (dalla nascita)
Calcola: stagionalità, momentum Antonacci, indicatori RAPTOR, livelli supporto
Nessuna sezione mediazione (a differenza di wheat.py).

Schedule:
- 05:30 CET: Analisi completa notturna + aggiornamento storico
- 16:45 CET: Rilevazione intra-day (segnali aggiornati)
- 17:00 CET: Chiusura giornaliera + salvataggio completo
"""

import json, math, os
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import yfinance as yf

# ── RILEVAMENTO ORARIO ─────────────────────────────────
def get_execution_type():
    """Determina il tipo di esecuzione basato sull'orario UTC"""
    now_utc = datetime.now(timezone.utc)
    hour = now_utc.hour
    minute = now_utc.minute
    
    # 05:30 CET = 04:30 UTC
    if 4 <= hour < 5 or (hour == 4 and minute >= 30):
        return 'morning'
    # 16:45 CET = 15:45 UTC
    elif 15 <= hour < 16 or (hour == 15 and minute >= 45):
        return 'intraday'
    # 17:00 CET = 16:00 UTC
    elif 16 <= hour < 17 or (hour == 16 and minute >= 0):
        return 'close'
    else:
        return 'manual'

# ── INDICATORI ────────────────────────────────────────
def calc_kama(closes, n=10, fast=2, slow=30):
    fsc = 2/(fast+1); ssc = 2/(slow+1)
    kama = [None]*len(closes)
    if len(closes) <= n: return kama
    kama[n] = closes[n]
    for i in range(n+1, len(closes)):
        d = abs(closes[i]-closes[i-n])
        v = sum(abs(closes[j]-closes[j-1]) for j in range(i-n+1, i+1))
        er = d/v if v else 0
        sc = (er*(fsc-ssc)+ssc)**2
        kama[i] = kama[i-1] + sc*(closes[i]-kama[i-1])
    return kama

def calc_rsi(closes, n=14):
    res = [None]*len(closes)
    for i in range(n+1, len(closes)):
        gs=[]; ls=[]
        for j in range(i-n, i+1):
            dd = closes[j]-closes[j-1]
            gs.append(max(dd,0)); ls.append(max(-dd,0))
        ag=sum(gs)/n; al=sum(ls)/n
        res[i] = round(100-100/(1+ag/al),2) if al>0 else 100.0
    return res

def calc_ao(highs, lows):
    mid = [(h+l)/2 for h,l in zip(highs,lows)]
    def ema(arr, p):
        k=2/(p+1); e=arr[0]; out=[e]
        for x in arr[1:]: e=x*k+e*(1-k); out.append(e)
        return out
    if len(mid)<13: return [0]*len(mid)
    e3=ema(mid,3); e13=ema(mid,13)
    return [round(a-b,4) for a,b in zip(e3,e13)]

def calc_sar(high, low, step=0.03, max_af=0.25):
    n=len(high); sar=[None]*n
    if n<5: return sar
    bull=high[1]>high[0]; af=step
    ep=max(high[:2]) if bull else min(low[:2])
    sar[1]=min(low[:2]) if bull else max(high[:2])
    for i in range(2,n):
        ps=sar[i-1]
        if bull:
            sar[i]=min(ps+af*(ep-ps), low[i-1], low[i-2] if i>=2 else low[i-1])
            if low[i]<sar[i]: bull=False; af=step; sar[i]=ep; ep=low[i]
            else:
                if high[i]>ep: ep=high[i]; af=min(af+step,max_af)
        else:
            sar[i]=max(ps+af*(ep-ps), high[i-1], high[i-2] if i>=2 else high[i-1])
            if high[i]>sar[i]: bull=True; af=step; sar[i]=ep; ep=high[i]
            else:
                if low[i]<ep: ep=low[i]; af=min(af+step,max_af)
    return sar

def calc_er(closes, n=10):
    res=[0]*len(closes)
    for i in range(n,len(closes)):
        d=abs(closes[i]-closes[i-n])
        v=sum(abs(closes[j]-closes[j-1]) for j in range(i-n+1,i+1))
        res[i]=round(d/v,4) if v else 0
    return res

def sanitize(obj):
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj): return None
        return obj
    if isinstance(obj, dict): return {k:sanitize(v) for k,v in obj.items()}
    if isinstance(obj, list): return [sanitize(v) for v in obj]
    return obj

# ── STAGIONALITÀ 25 ANNI ──────────────────────────────
def calc_stagionalita(closes, dates):
    """Rendimento medio mensile su 25 anni"""
    monthly_rets = defaultdict(list)
    for i in range(1, len(closes)):
        if closes[i] and closes[i-1]:
            month = int(dates[i][5:7])
            ret = (closes[i]-closes[i-1])/closes[i-1]*100
            monthly_rets[month].append(ret)

    # Rendimento cumulativo mensile medio
    stagionalita = []
    mesi = ['Gen','Feb','Mar','Apr','Mag','Giu','Lug','Ago','Set','Ott','Nov','Dic']
    for m in range(1,13):
        rets = monthly_rets[m]
        avg = sum(rets)/len(rets) if rets else 0
        positive = sum(1 for r in rets if r>0)
        wr = positive/len(rets)*100 if rets else 0
        stagionalita.append({
            'mese': m,
            'nome': mesi[m-1],
            'avg_ret': round(avg,3),
            'win_rate': round(wr,1),
            'n_anni': len(rets)
        })
    return stagionalita

# ── MOMENTUM ANTONACCI ────────────────────────────────
def calc_antonacci(closes, dates, lookback_months=12):
    """
    Dual Momentum assoluto: se il rendimento a 12 mesi > 0 → BUY, altrimenti → OUT
    """
    results = []
    approx_days = lookback_months * 21  # ~giorni di trading
    for i in range(approx_days, len(closes)):
        if closes[i] and closes[i-approx_days]:
            ret_12m = (closes[i]-closes[i-approx_days])/closes[i-approx_days]*100
            signal = 'BUY' if ret_12m > 0 else 'OUT'
            results.append({
                'date': dates[i],
                'price': closes[i],
                'ret_12m': round(ret_12m,2),
                'signal': signal
            })
    return results

# ── SUPPORTI ─────────────────────────────────────────
def find_supports(closes, dates, window=3):
    supports = []
    for i in range(window, len(closes)-window):
        if not closes[i]: continue
        is_min = all(closes[i] <= closes[i-j] for j in range(1,window+1) if closes[i-j]) and \
                 all(closes[i] <= closes[i+j] for j in range(1,window+1) if closes[i+j])
        if is_min:
            supports.append({'date': dates[i], 'price': closes[i]})
    return supports[-20:]  # ultimi 20 supporti

# ── MAIN ─────────────────────────────────────────────
def main():
    now = datetime.now()
    exec_type = get_execution_type()
    print(f"RAPTOR Microsoft Fetch — {now.strftime('%Y-%m-%d %H:%M')} [{exec_type.upper()}]")

    # ── Microsoft (NASDAQ) (25 anni) ─────────────
    print("Scarico Microsoft (MSFT)...")
    stk = yf.download("MSFT", start="2000-01-01", interval="1d",
                       auto_adjust=True, progress=False)

    # Appiattisci MultiIndex se presente (yfinance recente)
    if hasattr(stk.columns, 'levels'):
        stk.columns = stk.columns.get_level_values(0)
    stk_closes = [round(float(c),4) for c in stk['Close'].tolist()]
    stk_highs  = [round(float(c),4) for c in stk['High'].tolist()]
    stk_lows   = [round(float(c),4) for c in stk['Low'].tolist()]
    stk_volumes= [int(v) if v==v else 0 for v in stk['Volume'].tolist()]
    stk_dates  = [ts.strftime('%Y-%m-%d') for ts in stk.index]
    print(f"MSFT: {len(stk_closes)} barre ({stk_dates[0]} → {stk_dates[-1]})")

    # ── 3LMS.MI (dalla nascita) ───────────────────────
    print("Scarico 3LMS.MI...")
    etp = yf.download("3LMS.MI", start="2015-01-01", interval="1d",
                      auto_adjust=True, progress=False)

    if hasattr(etp.columns, 'levels'):
        etp.columns = etp.columns.get_level_values(0)
    etp_closes  = [round(float(c),4) for c in etp['Close'].tolist()]
    etp_highs   = [round(float(c),4) for c in etp['High'].tolist()]
    etp_lows    = [round(float(c),4) for c in etp['Low'].tolist()]
    etp_volumes = [int(v) for v in etp['Volume'].tolist()]
    etp_dates   = [ts.strftime('%Y-%m-%d') for ts in etp.index]
    print(f"3LMS: {len(etp_closes)} barre ({etp_dates[0]} → {etp_dates[-1]})")

    # ── ANALISI COMPLETA (MORNING + CLOSE) ─────────────
    if exec_type in ('morning', 'close', 'manual'):
        print(f"[{exec_type.upper()}] Calcolo analisi completa...")
        
        # KAMA su azione Microsoft
        stk_kama_fast = calc_kama(stk_closes, n=5,  fast=3, slow=20)
        stk_kama_slow = calc_kama(stk_closes, n=20, fast=2, slow=40)
        stk_rsi14     = calc_rsi(stk_closes, 14)
        stk_rsi5      = calc_rsi(stk_closes, 5)
        stk_ao        = calc_ao(stk_highs, stk_lows)
        stk_sar       = calc_sar(stk_highs, stk_lows)
        stk_er        = calc_er(stk_closes, 10)

        # Segnali RAPTOR su Microsoft (stessa logica di 3LMS)
        stk_signals = []
        stk_avg_vol = sum(stk_volumes[-21:-1])/20 if len(stk_volumes)>21 else 1
        for i in range(25, len(stk_closes)):
            kf=stk_kama_fast[i]; ks=stk_kama_slow[i]
            if kf is None or ks is None:
                stk_signals.append(None); continue
            p=stk_closes[i]
            if p>kf and kf>ks:   zona='LONG_CONF'
            elif p>kf and p>ks:  zona='LONG_EARLY'
            elif p<ks:           zona='STOP' if (ks-p)/ks*100>2 else 'USCITA'
            else:                zona='GRIGIA'
            vr=stk_volumes[i]/stk_avg_vol if stk_avg_vol>0 else 1
            gap_ok=ks>0 and abs(kf-ks)/ks>=0.003
            ao=stk_ao[i] if i<len(stk_ao) else 0
            sig=None
            baff=0
            for j in range(max(0,i-5),i+1):
                if stk_kama_fast[j] and stk_closes[j]>stk_kama_fast[j]: baff+=1
                else: baff=0
            if zona=='LONG_CONF' and ao>0 and vr>=2 and baff>=3 and stk_er[i]>=0.35 and gap_ok:
                sig='BUY3'
            elif zona=='LONG_EARLY' and ao>0 and vr>=1.5 and baff>=2 and stk_er[i]>=0.35:
                sig='BUY2'
            elif zona in ('STOP','USCITA'): sig='SELL'
            stk_signals.append(sig)
        stk_signals = [None]*25 + stk_signals  # padding per allineare a stk_closes

        # Stagionalità 25 anni
        stagionalita = calc_stagionalita(stk_closes, stk_dates)

        # Momentum Antonacci
        antonacci_full = calc_antonacci(stk_closes, stk_dates)
        antonacci_latest = antonacci_full[-1] if antonacci_full else {}

        # Supporti azione Microsoft
        stk_supports = find_supports(stk_closes, stk_dates)

        # Indicatori RAPTOR su 3LMS
        etp_kama_fast = calc_kama(etp_closes, n=5,  fast=3, slow=20)
        etp_kama_slow = calc_kama(etp_closes, n=20, fast=2, slow=40)
        etp_rsi14     = calc_rsi(etp_closes, 14)
        etp_rsi5      = calc_rsi(etp_closes, 5)
        etp_ao        = calc_ao(etp_highs, etp_lows)
        etp_sar       = calc_sar(etp_highs, etp_lows)
        etp_er        = calc_er(etp_closes, 10)

        # Segnali RAPTOR
        etp_signals = []
        avg_vol = sum(etp_volumes[-21:-1])/20 if len(etp_volumes)>21 else 1
        for i in range(25, len(etp_closes)):
            kf=etp_kama_fast[i]; ks=etp_kama_slow[i]
            if kf is None or ks is None:
                etp_signals.append(None); continue
            p=etp_closes[i]
            if p>kf and kf>ks:   zona='LONG_CONF'
            elif p>kf and p>ks:  zona='LONG_EARLY'
            elif p<ks:           zona='STOP' if (ks-p)/ks*100>2 else 'USCITA'
            else:                zona='GRIGIA'
            vr=etp_volumes[i]/avg_vol if avg_vol>0 else 1
            gap_ok=ks>0 and abs(kf-ks)/ks>=0.003
            ao=etp_ao[i] if i<len(etp_ao) else 0
            sig=None
            # Baff
            baff=0
            for j in range(max(0,i-5),i+1):
                if etp_kama_fast[j] and etp_closes[j]>etp_kama_fast[j]: baff+=1
                else: baff=0
            if zona=='LONG_CONF' and ao>0 and vr>=2 and baff>=3 and etp_er[i]>=0.35 and gap_ok:
                sig='BUY3'
            elif zona=='LONG_EARLY' and ao>0 and vr>=1.5 and baff>=2 and etp_er[i]>=0.35:
                sig='BUY2'
            elif zona in ('STOP','USCITA'): sig='SELL'
            etp_signals.append(sig)
        etp_signals = [None]*25 + etp_signals  # padding per allineare a etp_closes

        # Antonacci su 3LMS
        etp_antonacci = calc_antonacci(etp_closes, etp_dates)
        etp_antonacci_latest = etp_antonacci[-1] if etp_antonacci else {}

        # Supporti 3LMS
        etp_supports = find_supports(etp_closes, etp_dates)

        def fmt(arr):
            return [round(v,4) if v is not None else None for v in arr]

        output = sanitize({
            'execution_type': exec_type,
            'updated_at': now.isoformat(),
            'updated_display': now.strftime('%d/%m/%Y %H:%M'),

            # Microsoft (ultimi 3 anni per il grafico principale)
            'stk': {
                'dates':     stk_dates[-756:],
                'closes':    stk_closes[-756:],
                'highs':     stk_highs[-756:],
                'lows':      stk_lows[-756:],
                'volumes':   stk_volumes[-756:],
                'kama_fast': fmt(stk_kama_fast[-756:]),
                'kama_slow': fmt(stk_kama_slow[-756:]),
                'rsi14':     fmt(stk_rsi14[-756:]),
                'rsi5':      fmt(stk_rsi5[-756:]),
                'ao':        fmt(stk_ao[-756:]),
                'sar':       fmt(stk_sar[-756:]),
                'er':        stk_er[-756:],
                'signals':   stk_signals[-756:],
            },

            # Stagionalità (25 anni)
            'stagionalita': stagionalita,

            # Momentum Antonacci su azione Microsoft
            'antonacci_stk': antonacci_full[-252:],  # ultimo anno
            'antonacci_latest': antonacci_latest,

            # 3LMS completo
            'etp': {
                'dates':     etp_dates,
                'closes':    etp_closes,
                'highs':     etp_highs,
                'lows':      etp_lows,
                'volumes':   etp_volumes,
                'kama_fast': fmt(etp_kama_fast),
                'kama_slow': fmt(etp_kama_slow),
                'rsi14':     fmt(etp_rsi14),
                'rsi5':      fmt(etp_rsi5),
                'ao':        fmt(etp_ao),
                'sar':       fmt(etp_sar),
                'er':        etp_er,
                'signals':   etp_signals,
            },

            # Antonacci su 3LMS
            'antonacci_etp': etp_antonacci[-252:],
            'antonacci_etp_latest': etp_antonacci_latest,

            # Supporti
            'stk_supports': stk_supports,
            'etp_supports':  etp_supports,
        })

    # ── ANALISI LEGGERA INTRADAY (16:45) ────────────────
    else:  # intraday
        print(f"[INTRADAY] Calcolo segnali veloci...")
        
        # Carica il JSON precedente per mantenere storico
        try:
            with open('microsoft.json','r',encoding='utf-8') as f:
                output = json.load(f)
        except:
            output = {}

        # Aggiorna indicatori attuali — 3LMS
        etp_kama_fast = calc_kama(etp_closes, n=5,  fast=3, slow=20)
        etp_kama_slow = calc_kama(etp_closes, n=20, fast=2, slow=40)
        etp_rsi14     = calc_rsi(etp_closes, 14)
        etp_rsi5      = calc_rsi(etp_closes, 5)
        etp_ao        = calc_ao(etp_highs, etp_lows)
        etp_sar       = calc_sar(etp_highs, etp_lows)
        etp_er        = calc_er(etp_closes, 10)

        def calc_signals(closes, kama_fast, kama_slow, volumes, ao_arr, er_arr):
            signals = []
            avg_vol = sum(volumes[-21:-1])/20 if len(volumes)>21 else 1
            for i in range(25, len(closes)):
                kf=kama_fast[i]; ks=kama_slow[i]
                if kf is None or ks is None:
                    signals.append(None); continue
                p=closes[i]
                if p>kf and kf>ks:   zona='LONG_CONF'
                elif p>kf and p>ks:  zona='LONG_EARLY'
                elif p<ks:           zona='STOP' if (ks-p)/ks*100>2 else 'USCITA'
                else:                zona='GRIGIA'
                vr=volumes[i]/avg_vol if avg_vol>0 else 1
                gap_ok=ks>0 and abs(kf-ks)/ks>=0.003
                ao=ao_arr[i] if i<len(ao_arr) else 0
                sig=None
                baff=0
                for j in range(max(0,i-5),i+1):
                    if kama_fast[j] and closes[j]>kama_fast[j]: baff+=1
                    else: baff=0
                if zona=='LONG_CONF' and ao>0 and vr>=2 and baff>=3 and er_arr[i]>=0.35 and gap_ok:
                    sig='BUY3'
                elif zona=='LONG_EARLY' and ao>0 and vr>=1.5 and baff>=2 and er_arr[i]>=0.35:
                    sig='BUY2'
                elif zona in ('STOP','USCITA'): sig='SELL'
                signals.append(sig)
            return [None]*25 + signals

        etp_signals = calc_signals(etp_closes, etp_kama_fast, etp_kama_slow, etp_volumes, etp_ao, etp_er)

        # Aggiorna indicatori attuali — Microsoft
        stk_kama_fast = calc_kama(stk_closes, n=5,  fast=3, slow=20)
        stk_kama_slow = calc_kama(stk_closes, n=20, fast=2, slow=40)
        stk_rsi14     = calc_rsi(stk_closes, 14)
        stk_rsi5      = calc_rsi(stk_closes, 5)
        stk_ao        = calc_ao(stk_highs, stk_lows)
        stk_sar       = calc_sar(stk_highs, stk_lows)
        stk_er        = calc_er(stk_closes, 10)
        stk_signals   = calc_signals(stk_closes, stk_kama_fast, stk_kama_slow, stk_volumes, stk_ao, stk_er)

        def fmt(arr):
            return [round(v,4) if v is not None else None for v in arr]

        # Aggiorna il JSON con nuovi indicatori
        output['execution_type'] = exec_type
        output['updated_at'] = now.isoformat()
        output['updated_display'] = now.strftime('%d/%m/%Y %H:%M')

        output['etp']['dates'] = etp_dates
        output['etp']['closes'] = etp_closes
        output['etp']['highs'] = etp_highs
        output['etp']['lows'] = etp_lows
        output['etp']['volumes'] = etp_volumes
        output['etp']['kama_fast'] = fmt(etp_kama_fast)
        output['etp']['kama_slow'] = fmt(etp_kama_slow)
        output['etp']['rsi14'] = fmt(etp_rsi14)
        output['etp']['rsi5'] = fmt(etp_rsi5)
        output['etp']['ao'] = fmt(etp_ao)
        output['etp']['sar'] = fmt(etp_sar)
        output['etp']['er'] = etp_er
        output['etp']['signals'] = etp_signals

        output.setdefault('stk', {})
        output['stk']['dates'] = stk_dates[-756:]
        output['stk']['closes'] = stk_closes[-756:]
        output['stk']['highs'] = stk_highs[-756:]
        output['stk']['lows'] = stk_lows[-756:]
        output['stk']['volumes'] = stk_volumes[-756:]
        output['stk']['kama_fast'] = fmt(stk_kama_fast[-756:])
        output['stk']['kama_slow'] = fmt(stk_kama_slow[-756:])
        output['stk']['rsi14'] = fmt(stk_rsi14[-756:])
        output['stk']['rsi5'] = fmt(stk_rsi5[-756:])
        output['stk']['ao'] = fmt(stk_ao[-756:])
        output['stk']['sar'] = fmt(stk_sar[-756:])
        output['stk']['er'] = stk_er[-756:]
        output['stk']['signals'] = stk_signals[-756:]

        output = sanitize(output)

    os.makedirs('data', exist_ok=True)
    with open('microsoft.json','w',encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',',':'), allow_nan=False)
    print(f"✅ microsoft.json aggiornato [{exec_type}]")

if __name__ == '__main__':
    main()
