"""Трекинг очереди у Ларака: сопоставление крупных судов между соседними проходами.
Вход: записи проходов {date, orbit, time, n_all, ships:[(lat,lon,len_m),...]}.
Правила (как в эталонном расчёте ревьюера):
  крупное = len >= 250 м; кандидат для сопоставления = len >= 200 м;
  то же судно = расстояние < MATCH_KM (0.8) [+ v1.1: длина в пределах ±25%];
  пара проходов учитывается, если интервал 1..6 дней; проходы с n_all < 10 отбрасываются.
Выход на пару: large, stay (крупные, найденные в пред. проходе), gone (крупные пред. прохода, не найденные сейчас), gap_days.
Месяц/неделя: gone_per_day = sum(gone)/sum(gap); stay_share = sum(stay)/sum(large); dwell_days = mean(large)/gone_per_day.
"""
import math
MATCH_KM=0.8
def dist(a,b): return math.hypot((a[1]-b[1])*111.32*math.cos(math.radians(26.95)),(a[0]-b[0])*111.32)
def same(a,b,len_tol=None):
    if dist(a,b)>=MATCH_KM: return False
    return True if len_tol is None else abs(a[2]-b[2])<=len_tol*max(a[2],b[2])
def pair(prev,cur,len_tol=None):
    S=[s for s in cur['ships'] if s[2]>=250]; C=[s for s in cur['ships'] if s[2]>=200]
    Pb=[s for s in prev['ships'] if s[2]>=250]; P=[s for s in prev['ships'] if s[2]>=200]
    stay=sum(1 for s in S if any(same(s,p,len_tol) for p in P))
    gone=sum(1 for p in Pb if not any(same(p,c,len_tol) for c in C))
    return dict(large=len(S),stay=stay,gone=gone)
