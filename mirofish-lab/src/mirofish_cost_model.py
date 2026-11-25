"""Independent re-implementation of MiroFish's activation rule + a token/cost model.
Not MiroFish code. Assumptions documented inline."""
import random, statistics as st
random.seed(7)
N=56
def population():
    ag=[]
    # 40 retail individuals (prompt: activity 0.6-0.9, evening 18-23); 8 opinion leaders/media (0.4-0.6, 8-23); 8 institutions (0.1-0.3, 9-17)
    for i in range(40): ag.append(dict(t='retail',act=random.uniform(.6,.9),hours=set(range(18,24)) if random.random()<.5 else set([9,10,11,12,13]+list(range(18,24)))))
    for i in range(8): ag.append(dict(t='kol',act=random.uniform(.4,.6),hours=set(range(8,24))))
    for i in range(8): ag.append(dict(t='inst',act=random.uniform(.1,.3),hours=set(range(9,18))))
    return ag
PEAK={19,20,21,22}; OFF={0,1,2,3,4,5}
def active_counts(ag,rounds,amin,amax,mpr=60):
    out=[]
    for r in range(rounds):
        h=(r*mpr//60)%24
        mult=1.5 if h in PEAK else (0.05 if h in OFF else 1.0)
        target=int(random.uniform(amin,amax)*mult)
        cand=[i for i,a in enumerate(ag) if h in a['hours'] and random.random()<a['act']]
        out.append(random.sample(cand,min(target,len(cand))))
    return out
# token assumptions (per call)
SYS=2800          # persona bio+persona (~2000 字/words) + OASIS wrapper
TOOLS={'twitter':700,'reddit':1500}
FEED={'twitter':900,'reddit':1800}   # 5 posts JSON incl. comments / scores
TURN_OUT=180      # tool-call output tokens
TURN_MEM=lambda p: FEED[p]+80+TURN_OUT+60  # what each activation adds to CAMEL ChatHistoryMemory
def sim_tokens(sel,platform):
    k=[0]*N; tin=tout=calls=0
    for s in sel:
        for i in s:
            tin+=SYS+TOOLS[platform]+FEED[platform]+80+k[i]*TURN_MEM(platform)
            tout+=TURN_OUT; calls+=1; k[i]+=1
    return tin,tout,calls,k
def prep_tokens():
    # ontology 1, profiles N (+~10% retries), config: time 1, event 1, agent batches ceil(N/15)
    calls=1+int(N*1.1)+2+(-(-N//15))
    tin=17000 + int(N*1.1)*2500 + 4000+3500 + 4*3500
    tout=2500 + int(N*1.1)*2600 + 300+1200 + 4*2600
    return tin,tout,calls
def report_tokens(k_tw,k_rd,sections=4):
    # outline 1; per section ~5 ReACT iterations (growing context ~8-20k), 1 insight_forge (1 LLM),
    # 1 interview_agents (3 LLM + 5 agents x 2 platforms interviews carrying full agent memory)
    avg_mem=lambda k,p: SYS+st.mean(k)*TURN_MEM(p)
    tin=6000; tout=600; calls=1
    for s in range(sections):
        tin+=5*(9000+3000*s); tout+=5*400+1500; calls+=5
        tin+=2500; tout+=300; calls+=1
        tin+=3*5000; tout+=3*800; calls+=3
        tin+=5*(avg_mem(k_tw,'twitter')+avg_mem(k_rd,'reddit')); tout+=10*400; calls+=10
    return tin,tout,calls
PRICES={'qwen-plus (intl, ~$0.40/$1.20)':(0.40,1.20),'qwen-plus (CN, ~$0.11/$0.28)':(0.11,0.28),
        'gpt-4o-mini ($0.15/$0.60)':(0.15,0.60),'GPT-4o ($2.50/$10)':(2.5,10),
        'Claude Sonnet 5.5 ($2/$10)':(2,10),'Claude Sonnet 4.6 ($3/$15)':(3,15),'Claude Haiku 5.5 ($0.10/$0.50)':(0.10,0.50)}
for label,(amin,amax) in [('LLM-example time cfg 5-50',(5,50)),('fallback time cfg 3-11',(3,11))]:
    print(f"\n=== {label}; N={N}; platform=parallel (Twitter+Reddit) ===")
    for R in (10,40,72):
        reps=[]
        for rep in range(200):
            ag=population()
            tw=sim_tokens(active_counts(ag,R,amin,amax),'twitter')
            rd=sim_tokens(active_counts(ag,R,amin,amax),'reddit')
            pr=prep_tokens(); rp=report_tokens(tw[3],rd[3])
            reps.append((tw,rd,pr,rp))
        tin=st.mean(t[0][0]+t[1][0]+t[2][0]+t[3][0] for t in reps)
        tout=st.mean(t[0][1]+t[1][1]+t[2][1]+t[3][1] for t in reps)
        simcalls=st.mean(t[0][2]+t[1][2] for t in reps)
        allcalls=st.mean(t[0][2]+t[1][2]+t[2][2]+t[3][2] for t in reps)
        simin=st.mean(t[0][0]+t[1][0] for t in reps)
        print(f"R={R:3d}: sim agent-calls={simcalls:6.0f} total LLM calls={allcalls:6.0f} input={tin/1e6:5.2f}M (sim {simin/1e6:4.2f}M) output={tout/1e6:4.2f}M | "+
              " | ".join(f"{k.split(' (')[0]}{'' if 'qwen' not in k else k[k.find('('):k.find(',')]+')'}: ${tin/1e6*p[0]+tout/1e6*p[1]:.2f}" for k,p in PRICES.items()))
