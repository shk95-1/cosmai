import sys,csv,collections; sys.path.insert(0,'/home/user1/github_prj/Main/architect/slice-p9-wish-mining')
import wish_extractor as W
f=sys.argv[1]; show=len(sys.argv)>2
rows=list(csv.DictReader(open(f)))
cm=collections.Counter(); errs=[]
for r in rows:
    p,m,s=W.classify(r['text']); cm[(r['gold'],p)]+=1
    if p!=r['gold']: errs.append((r['gold'],p,m,r['text'][:110]))
n=len(rows)
print('n',n,'acc %.2f'%(sum(v for (g,p),v in cm.items() if g==p)/n))
for c in 'abcn':
    tp=cm[(c,c)]; fp=sum(v for (g,p),v in cm.items() if p==c and g!=c); fn=sum(v for (g,p),v in cm.items() if g==c and p!=c)
    print(c,'P=%.2f R=%.2f'%(tp/max(1,tp+fp),tp/max(1,tp+fn)),'tp',tp,'fp',fp,'fn',fn)
if show:
    for e in errs: print(e)
