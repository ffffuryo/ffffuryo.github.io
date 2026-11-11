import numpy as np, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pitch import *
import os
D=os.path.dirname(os.path.abspath(__file__)); IMG=os.path.join(D,'..','img')
plt.rcParams.update({'font.family':'IPAPGothic','axes.unicode_minus':False,'figure.facecolor':'white',
 'axes.facecolor':'white','axes.spines.top':False,'axes.spines.right':False,'font.size':11})
TEAL,ORG,GRN,GRY='#0E6E76','#B5541F','#1F7A5C','#8A93A1'
def save(fig,n): fig.savefig(os.path.join(IMG,n),dpi=200,facecolor='white'); plt.close(fig)
YP=18.44
# sign check
v0=np.array([0,40.,0])
for sx in (+1,-1):
    cr=np.cross(np.array([sx,0,0.]),v0)
    print('omega axis',sx,'x : w x v =',cr,'-> Magnus force (up if +z)')
w0=init()[6:]; p0=parts(init()[3:6],w0)
print({k:v for k,v in p0.items() if k not in('Fdv','Fiv','dw')}, 'mg',m*9.81, 'Fiv',p0['Fiv'])
# integrators
res={}
for name,step,dt in [('code',step_code,.001),('euler',step_euler,.001),('rk4',step_rk4,.001),('rk4_dt01',step_rk4,.01)]:
    n=int(round(0.8/dt)); T,X=run(step,dt,n)
    t10,s10=at_y(T,X,10); tp,sp_=at_y(T,X,YP)
    res[name]=dict(t10=t10,z10=s10[2],tp=tp,zp=sp_[2],wp=sp_[6:],T=T,X=X)
    print(name,t10,s10[2],tp,sp_[2])
# B trajectories
def traj(**kw): return run(step_rk4,.001,800,until_y=YP,**kw)
Ta,Xa=traj(); Tn,Xn=traj(drag=False,magnus=False); Td,Xd=traj(magnus=False)
tp_a,sa=at_y(Ta,Xa,YP); tp_n,sn=at_y(Tn,Xn,YP); tp_d,sd=at_y(Td,Xd,YP)
fig,ax=plt.subplots(figsize=(8,4.5))
def cut(X): 
    k=np.argmax(X[:,1]>YP)+1; return X[:k,1],X[:k,2]
ax.plot(*cut(Xa),color=TEAL,lw=2.5,label='すべての力（重力＋空気抵抗＋マグヌス力）')
ax.plot(*cut(Xn),color=GRY,lw=2,ls='--',label='空気なし（重力のみ）')
ax.plot(*cut(Xd),color=ORG,lw=2,label='空気抵抗あり・マグヌス力なし')
for y,l in [(10,'コードの判定位置 y=10m'),(YP,'本塁 18.44m')]:
    ax.axvline(y,color=GRY,ls=':',lw=1.2); ax.text(y-0.15 if y<15 else y+0.15,0.63 if y<15 else 1.88,l,rotation=90,ha='right' if y<15 else 'left',va='bottom' if y<15 else 'top',color='#444',fontsize=10)
ax.set_xlim(0,19.2);ax.set_ylim(0.6,1.9)
ax.set_xlabel('本塁方向の距離 y [m]');ax.set_ylabel('高さ z [m]');ax.legend(loc='lower left',frameon=False,fontsize=10)
ax.set_title('投球の軌道（横から見た図）',loc='left'); fig.tight_layout(); save(fig,'trajectory_side.png')
# A Cd-Re
Re=np.logspace(2,np.log10(3e6),600); Rp=p0['Re']
fig,ax=plt.subplots(figsize=(8,4.5))
ax.semilogx(Re,Cd(Re),color=TEAL,lw=2.8,label='Cd（合計）')
lab=['(i) 24/Re','(ii) 2.6(Re/5)/(1+(Re/5)^1.52)','(iii) 臨界付近の急落項','(iv) 0.25(Re/1e6)/(1+Re/1e6)']
for t,l,c in zip(Cd_terms(Re),lab,[ORG,GRN,GRY,'#444']): ax.plot(Re,t,ls='--',lw=1,color=c,label=l)
ax.axvline(Rp,color=ORG,ls=':');ax.axvline(2.63e5,color=GRY,ls=':')
ax.plot([Rp],[Cd(Rp)],'o',color=ORG)
ax.annotate(f'投球 40 m/s\nRe≈{Rp:.2e}\nCd≈{Cd(Rp):.2f}',(Rp,Cd(Rp)),(1e4,0.8),arrowprops=dict(arrowstyle='->',color=ORG),color=ORG,fontsize=10)
ax.text(2.63e5*1.08,0.62,'Re=2.63e5\n（臨界点）',color='#555',fontsize=10)
ax.set_ylim(0,1.6);ax.set_xlim(1e2,3e6)
ax.set_xlabel('レイノルズ数 Re（対数目盛）');ax.set_ylabel('抗力係数 Cd');ax.legend(frameon=False,fontsize=9,loc='upper left',bbox_to_anchor=(0.12,1))
fig.tight_layout();save(fig,'cd_re.png')
# C integrator compare
names=[('code','(1) コードのRK4'),('euler','(2) オイラー'),('rk4','(3) 正式RK4'),('rk4_dt01','(4) 正式RK4 dt=0.01')]
cols=[ORG,GRY,TEAL,GRN]
fig,axs=plt.subplots(1,2,figsize=(8,4.5))
for ax,(key,ttl) in zip(axs,[('z','z [m]'),('t','t [s]')]):
    pass
rows=[]
for k,l in names:
    r_=res[k]; rows.append([l,f"{r_['z10']:.4f}",f"{r_['t10']:.4f}",f"{r_['zp']:.4f}",f"{r_['tp']:.4f}",
      f"{(r_['z10']-res['rk4']['z10'])*1e3:+.2f}",f"{(r_['zp']-res['rk4']['zp'])*1e3:+.2f}"])
plt.close(fig)
fig,ax=plt.subplots(figsize=(8,4.5)); ax.axis('off')
ax.set_title('積分法の比較（(3)を基準）',loc='left')
tb=ax.table(cellText=rows,colLabels=['積分法','y=10m\nz [m]','y=10m\nt [s]','本塁\nz [m]','本塁\nt [s]','z差 (10m)\n[mm]','z差 (本塁)\n[mm]'],
  loc='center',cellLoc='center',colWidths=[.25,.12,.12,.12,.12,.13,.13])
tb.auto_set_font_size(False);tb.set_fontsize(10);tb.scale(1,2.6)
for (i,j),c in tb.get_celld().items():
    c.set_edgecolor('#cccccc')
    if i==0: c.set_facecolor(TEAL);c.get_text().set_color('white')
    elif i==3: c.set_facecolor('#E3F0F1')
fig.text(0.05,0.12,'差 = その積分法 − (3)。y=10m・本塁は隣接ステップ間の線形補間。',fontsize=9,color='#555')
save(fig,'integrator_compare.png')
# D forces
fig,ax=plt.subplots(figsize=(8,4.5))
vals=[m*9.81,p0['Fd'],p0['Fi']]; labs=['重力','空気抵抗','マグヌス力']
b=ax.barh(labs[::-1],vals[::-1],color=[ORG,GRN,TEAL][::-1] if False else [TEAL,GRN,GRY],height=.55)
for rect,vv in zip(b,vals[::-1]): ax.text(vv+.03,rect.get_y()+rect.get_height()/2,f'{vv:.2f} N',va='center')
ax.set_xlim(0,max(vals)*1.2);ax.set_xlabel('力の大きさ [N]（リリース時）');ax.set_title('リリース直後の力の大きさ',loc='left')
fig.tight_layout();save(fig,'forces_bar.png')
# E spin
Tw=Ta; wr=np.linalg.norm(Xa[:,6:],axis=1)/RPM
tt=np.linspace(0,Ta[-1],100)
fig,ax=plt.subplots(figsize=(8,4.5))
ax.plot(Ta[:np.argmax(Xa[:,1]>YP)+1],wr[:np.argmax(Xa[:,1]>YP)+1],color=TEAL,lw=2.5,label='このモデル')
ax.plot(tt,2400*np.exp(-tt/25),color=GRY,ls='--',lw=2,label='実測に基づく目安（時定数≈25 s, Nathanら）')
ax.set_ylim(2300,2420);ax.set_xlabel('時間 t [s]');ax.set_ylabel('回転数 ω [rpm]');ax.legend(frameon=False,loc='lower left')
ax.set_title('飛行中の回転数の変化',loc='left');fig.tight_layout();save(fig,'spin_decay.png')
# results
wpl=np.linalg.norm(res['rk4']['wp'])/RPM
dropg=1.8-sn[2]; mag=(sa[2]-sd[2])
L=lambda k,r_: f"| {k} | {r_['t10']:.4f} | {r_['z10']:.4f} | {r_['tp']:.4f} | {r_['zp']:.4f} | {(r_['z10']-res['rk4']['z10'])*1e3:+.2f} | {(r_['zp']-res['rk4']['zp'])*1e3:+.2f} |"
md=f"""# 投球モデル 数値計算結果

条件: m=0.145 kg, r=0.0365 m, 初速 40 m/s（+y方向）, 2400 rpm バックスピン, 初期高さ 1.8 m, dt=0.001 s, 800ステップ。
y=10 m・18.44 m での t, z は隣り合うステップ間の線形補間。

## バックスピンの符号確認
- 回転軸 +x（ω=+2400 rpm の x成分）: ω×v = {np.cross(np.array([1.,0,0]),v0)} → +z 向き。
- 回転軸 −x: ω×v = {np.cross(np.array([-1.,0,0]),v0)} → −z 向き（トップスピン）。
- モデルは Fi を引く式で、Fi の向きは (v×ω)/|v×ω| なので、ボールが受ける力は ω×v の向き。よって上向きのマグヌス力には ω=+x（成分 +2400 rpm）が正しい（軸 −x ではない）。
- 確認: ボール上端(r=+z)の速度 ω×r = +x̂×ẑ = −ŷ（進行方向と逆）→ バックスピン。リリース時にボールが受けるマグヌス力ベクトル = {-p0['Fiv']} N（z成分が正＝上向き。式中の Fiv={p0['Fiv']} は引かれる側）。

## リリース時の値（v=40 m/s, ω=2400 rpm）
| 量 | 値 |
|---|---|
| スピンパラメータ S | {p0['S']:.4f} |
| レイノルズ数 Re | {p0['Re']:.4e} |
| 抗力係数 Cd | {p0['Cd']:.4f} |
| 揚力係数 Ci=0.09+0.6S | {p0['Ci']:.4f} |
| 重力 mg | {m*9.81:.4f} N |
| 空気抵抗 Fd | {p0['Fd']:.4f} N |
| マグヌス力 Fi | {p0['Fi']:.4f} N |
| Fi / mg | {p0['Fi']/(m*9.81):.3f} |
| モーメント係数 Cm | {p0['Cm']:.3e} |

## 積分法の比較
| 積分法 | t(10m) [s] | z(10m) [m] | t(本塁) [s] | z(本塁) [m] | z差 10m [mm] | z差 本塁 [mm] |
|---|---|---|---|---|---|---|
{L('(1) コードのRK4',res['code'])}
{L('(2) オイラー',res['euler'])}
{L('(3) 正式RK4 dt=0.001',res['rk4'])}
{L('(4) 正式RK4 dt=0.01',res['rk4_dt01'])}

z差は (3) を基準とした差。(1)−(3) は 10m で {(res['code']['z10']-res['rk4']['z10'])*1e3:+.3f} mm, 本塁で {(res['code']['zp']-res['rk4']['zp'])*1e3:+.3f} mm。

## 重力による落下とマグヌス力による浮き上がり（正式RK4, dt=0.001）
- 空気なし（重力のみ）: 本塁到達 t={tp_n:.4f} s, z={sn[2]:.4f} m（落下 {dropg*100:.1f} cm）
- 空気抵抗のみ（マグヌスなし）: t={tp_d:.4f} s, z={sd[2]:.4f} m（落下 {(1.8-sd[2])*100:.1f} cm）
- 全ての力: t={tp_a:.4f} s, z={sa[2]:.4f} m（落下 {(1.8-sa[2])*100:.1f} cm）
- マグヌスによる浮き上がり（全力 − マグヌスなし, 本塁 z差）: {mag*100:.1f} cm
- 空気なしの重力落下 ½g t²（t={tp_n:.3f} s）= {0.5*9.81*tp_n**2*100:.1f} cm（自己整合確認: 上の落下量と一致）

## スピンの減衰（本塁到達時）
- 初期 2400.0 rpm → {wpl:.2f} rpm（{(2400-wpl):.2f} rpm 減, {(2400-wpl)/2400*100:.3f} %）
- 参考: 時定数≈25 s の指数減衰なら t={tp_a:.3f} s で {2400*np.exp(-tp_a/25):.1f} rpm（{(1-np.exp(-tp_a/25))*100:.2f} %減）。この25 sは文献（Nathanら）に基づく概算の目安値。
"""
open(os.path.join(D,'results.md'),'w').write(md)
print(md)
