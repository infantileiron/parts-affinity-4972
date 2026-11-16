import math,json,hashlib
from itertools import combinations
from src.constants.enums import HOLDOUT_RATIO,HOLDOUT_MIN_JOBS
VERSION='apriori-max3-v1'
HOLDOUT_VERSION='holdout-recompute-v1'
def mine(rows,min_support=.15,min_confidence=.6,min_lift=1):
 transactions=[frozenset(r['parts']) for r in rows if r.get('parts')]
 n=len(transactions)
 if n<2:raise ValueError('至少需要2条含备件的有效维保作业')
 if not 0<min_support<=1 or not 0<min_confidence<=1 or min_lift<0:raise ValueError('无效阈值')
 minimum=math.ceil(n*min_support-1e-12);universe=sorted(set().union(*transactions))
 counts={};level=[]
 for part in universe:
  key=frozenset([part]);count=sum(key<=t for t in transactions)
  if count>=minimum:counts[key]=count;level.append(key)
 for size in (2,3):
  candidates={a|b for a in level for b in level if len(a|b)==size}
  candidates={c for c in candidates if all(frozenset(s) in counts for s in combinations(sorted(c),size-1))}
  level=[]
  for c in sorted(candidates,key=lambda c:tuple(sorted(c))):
   count=sum(c<=t for t in transactions)
   if count>=minimum:counts[c]=count;level.append(c)
 rules=[]
 for itemset,count in counts.items():
  if len(itemset)<2:continue
  for size in range(1,len(itemset)):
   for a_tuple in combinations(sorted(itemset),size):
    a=frozenset(a_tuple);b=itemset-a;support=count/n;confidence=count/counts[a];lift=confidence/(counts[b]/n)
    if confidence+1e-12>=min_confidence and lift+1e-12>=min_lift:
     rules.append({'signature':','.join(sorted(a))+'->'+','.join(sorted(b)),'antecedent':sorted(a),'consequent':sorted(b),'count':count,'support':support,'confidence':confidence,'lift':lift})
 rules.sort(key=lambda r:(-r['lift'],-r['confidence'],-r['support'],r['signature']))
 frequent=[{'items':sorted(k),'count':v,'support':v/n} for k,v in counts.items()]
 return dict(valid_jobs=n,excluded=len(rows)-n,minimum_count=minimum,frequent=frequent,rules=rules,version=VERSION,checksum=checksum(rows),method='Apriori最大项集长度3；每张有效作业为一个事务，重复备件去重；support=count(A∪B)/N；confidence=count(A∪B)/count(A)；lift=confidence/support(B)。挖掘仅使用发现集作业，留验集在规则封存前不参与筛选。关联不代表必须联换，需核对设备、兼容性与备件数量。')
def checksum(rows):
 return hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def split_cohorts(rows,ratio=HOLDOUT_RATIO):
 # 按设备分层，层内按作业稳定排序后等距抽取留验集；同一输入永远得到同一归属
 buckets={}
 for r in rows:buckets.setdefault(r.get('equipment') or '',[]).append(r)
 out=[]
 for equipment in sorted(buckets):
  group=sorted(buckets[equipment],key=lambda r:(r.get('id') or 0,r.get('code') or ''))
  m=len(group);h=round(m*ratio)
  holdout_idx={min(m-1,math.floor((j+0.5)*m/h)) for j in range(h)} if h else set()
  for p,r in enumerate(group):
   marked=dict(r);marked['cohort']='holdout' if p in holdout_idx else 'discovery';out.append(marked)
 return out
def evaluate_holdout(rules,holdout_rows,min_support,min_confidence,min_lift):
 transactions=[frozenset(r['parts']) for r in holdout_rows if r.get('parts')]
 n=len(transactions);excluded=len(holdout_rows)-n;items=[]
 for rule in rules:
  a=frozenset(rule['antecedent']);b=frozenset(rule['consequent']);ab=a|b
  count_a=sum(a<=t for t in transactions);count_b=sum(b<=t for t in transactions);count_ab=sum(ab<=t for t in transactions)
  if n<HOLDOUT_MIN_JOBS or count_a==0:
   verdict='insufficient_sample';support=confidence=lift=None
  else:
   support=count_ab/n;confidence=count_ab/count_a;lift=confidence/(count_b/n) if count_b else 0.0
   verdict='passed' if support+1e-12>=min_support and confidence+1e-12>=min_confidence and lift+1e-12>=min_lift else 'failed'
  items.append({'signature':rule['signature'],'rule_id':rule.get('id'),'count':count_ab,'antecedent_count':count_a,'consequent_count':count_b,'support':support,'confidence':confidence,'lift':lift,'verdict':verdict})
 overall='insufficient_sample' if n<HOLDOUT_MIN_JOBS else ('passed' if items and all(x['verdict']=='passed' for x in items) else 'failed')
 return {'version':HOLDOUT_VERSION,'holdout_valid_jobs':n,'holdout_excluded':excluded,'thresholds':{'min_support':min_support,'min_confidence':min_confidence,'min_lift':min_lift},'items':items,'verdict':overall,'method':'封存后仅评估既定规则：按留验集作业重算共同次数与support/confidence/lift，沿用建组阈值判定通过、未达标或样本不足；留验作业从未参与规则筛选。'}
