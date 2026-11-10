import math, sys, json, numpy as np, rasterio, pystac_client, planetary_computer as pc, os
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from scipy import ndimage
AOI=(56.30,26.84,56.60,27.03); ZONE=(56.33,26.86,56.58,27.02); RES=20.0
W=int((AOI[2]-AOI[0])*111320*math.cos(math.radians(26.94))/RES); H=int((AOI[3]-AOI[1])*111320/RES)
T=from_bounds(*AOI,W,H)
LAND=np.load('mask_land.npy'); STATIC=np.load('mask_static.npy'); assert LAND.shape==(H,W)
cat=pystac_client.Client.open('https://planetarycomputer.microsoft.com/api/stac/v1', modifier=pc.sign_inplace)
d0,d1=sys.argv[1],sys.argv[2]; out=sys.argv[3]
items=sorted(cat.search(collections=['sentinel-1-rtc'],bbox=ZONE,datetime=f'{d0}/{d1}',max_items=200).items(),key=lambda x:x.datetime)
seen=set()
for it in items:
    key=(it.datetime.strftime('%Y-%m-%d'),it.properties.get('sat:relative_orbit'))
    if key in seen: continue
    try:
        with rasterio.open(it.assets['vv'].href) as s:
            with WarpedVRT(s,crs='EPSG:4326',transform=T,width=W,height=H,resampling=Resampling.average,nodata=np.nan) as v: a=v.read(1)
    except Exception as e: print('err',it.id,e); continue
    valid=np.isfinite(a)&(a>0)
    zc0=int((ZONE[0]-AOI[0])/(AOI[2]-AOI[0])*W); zc1=int((ZONE[2]-AOI[0])/(AOI[2]-AOI[0])*W)
    zr0=int((AOI[3]-ZONE[3])/(AOI[3]-AOI[1])*H); zr1=int((AOI[3]-ZONE[1])/(AOI[3]-AOI[1])*H)
    cov=valid[zr0:zr1,zc0:zc1].mean()
    if cov<0.9: continue
    seen.add(key)
    db=10*np.log10(np.where(valid,a,1e-6))
    sea=valid&~LAND&~STATIC
    med=np.median(db[sea]); sd=np.std(db[sea][db[sea]<med+6]); thr=med+max(10,5*sd)
    if not np.isfinite(thr): continue
    lab,n=ndimage.label((db>thr)&sea); L=[]; P=[]
    for i in range(1,n+1):
        ys,xs=np.where(lab==i)
        if len(ys)<4 or len(ys)>500: continue
        cy,cx=ys.mean(),xs.mean()
        if not (zr0<=cy<zr1 and zc0<=cx<zc1): continue
        q=np.c_[xs,ys].astype(float); q-=q.mean(0)
        L.append(int((np.ptp(q@np.linalg.eigh(np.cov(q.T))[1][:,-1])+1)*RES)); P.append((round(float(AOI[3]-(cy+.5)/H*(AOI[3]-AOI[1])),5),round(float(AOI[0]+(cx+.5)/W*(AOI[2]-AOI[0])),5),L[-1]))
    rec=dict(date=key[0],orbit=key[1],time=it.datetime.strftime('%H:%M'),sat=it.id[:3],n_all=len(L),n150=sum(l>=150 for l in L),n250=sum(l>=250 for l in L),thr=round(float(thr),1),ships=P)
    with open(out,'a') as f: f.write(json.dumps(rec)+'\n')
    print(rec,flush=True)
