import numpy as np
m=0.145; r=0.0365; d=2*r; A=np.pi*r**2; I=0.4*m*r**2
rho=1.205; mu=1.822e-5; g=np.array([0,0,-9.81])
RPM=2*np.pi/60
def Cd_terms(Re):
    return (24/Re, 2.6*(Re/5)/(1+(Re/5)**1.52),
            0.411*(Re/2.63e5)**-7.94/(1+(Re/2.63e5)**-8.00),
            0.25*(Re/1e6)/(1+Re/1e6))
def Cd(Re): return sum(Cd_terms(Re))
def parts(v,w,drag=True,magnus=True):
    sp=np.linalg.norm(v); wn=np.linalg.norm(w)
    S=r*np.linalg.norm(np.cross(w,v))/sp**2
    Re=rho*sp*d/mu
    cd=Cd(Re); Fd=0.5*cd*rho*A*sp**2
    Fdv=0.5*cd*rho*A*sp*v if drag else np.zeros(3)
    vw=np.cross(v,w); nvw=np.linalg.norm(vw)
    Ci=0.09+0.6*S; Fi=0.5*Ci*rho*A*sp**2
    Fiv=Fi*vw/nvw if (magnus and nvw>0) else np.zeros(3)
    Cm=2e-5*S
    dw=-(0.5*Cm*rho*A*sp**2*d/I)*w/wn if wn>0 else np.zeros(3)
    return dict(S=S,Re=Re,Cd=cd,Fd=Fd,Ci=Ci,Fi=Fi,Cm=Cm,Fdv=Fdv,Fiv=Fiv,dw=dw)
def rates(st,**kw):
    s,v,w=st[:3],st[3:6],st[6:]
    p=parts(v,w,**kw)
    return np.concatenate([v,(m*g-p['Fdv']-p['Fiv'])/m,p['dw']])
def step_code(st,dt,**kw):
    a=rates(st,**kw); new=st.copy()
    for sl in (slice(0,3),slice(3,6),slice(6,9)):
        a0=a[sl]; k1=a0*dt; k2=(a0+k1/2)*dt; k3=(a0+k2/2)*dt; k4=(a0+k3)*dt
        new[sl]=st[sl]+(k1+2*k2+2*k3+k4)/6
    return new
def step_euler(st,dt,**kw): return st+rates(st,**kw)*dt
def step_rk4(st,dt,**kw):
    f=lambda x: rates(x,**kw)
    k1=f(st);k2=f(st+dt/2*k1);k3=f(st+dt/2*k2);k4=f(st+dt*k3)
    return st+dt/6*(k1+2*k2+2*k3+k4)
def init(rpm=2400,sign=+1):
    return np.concatenate([[0,0,1.8],[0,40,0],[sign*rpm*RPM,0,0]])
def run(step,dt,n,st0=None,until_y=None,**kw):
    st=init() if st0 is None else st0
    T=[0.0];X=[st]
    for i in range(n):
        st=step(st,dt,**kw); T.append((i+1)*dt); X.append(st)
        if until_y is not None and st[1]>until_y: break
    return np.array(T),np.array(X)
def at_y(T,X,y):
    i=np.argmax(X[:,1]>y)
    f=(y-X[i-1,1])/(X[i,1]-X[i-1,1])
    return T[i-1]+f*(T[i]-T[i-1]), X[i-1]+f*(X[i]-X[i-1])
