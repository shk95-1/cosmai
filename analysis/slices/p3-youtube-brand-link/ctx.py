import csv,re,sys,random
csv.field_size_limit(10**9)
random.seed(0)
words=sys.argv[1].split(',')
k=int(sys.argv[2]) if len(sys.argv)>2 else 3
PART=r'(?:이에요|예요|이고|이랑|이라고|이라는|이라서|이니까|인데|입니다|이야|이죠|이네|으로|에서|처럼|보다|밖에|부터|까지|는|은|이|가|을|를|도|의|로|와|과|에|랑|만|나|든|요|야|죠|네|거|꺼|건|껀|게|께)?'
docs=[r['full_text'] for r in csv.DictReader(open('data/transcripts.csv'))]
docs+=[r['text'] for r in csv.DictReader(open('data/comments.csv'))]
for w in words:
    rx=re.compile(r'(?<![가-힣A-Za-z0-9])('+re.escape(w)+r')'+PART+r'(?=$|[^가-힣A-Za-z0-9])',re.I)
    ex=[]
    for d in docs:
        for m in rx.finditer(d):
            ex.append(d[max(0,m.start()-30):m.end()+30].replace('\n',' '))
    random.shuffle(ex)
    print('##',w,len(ex))
    for e in ex[:k]: print('   ',e)
