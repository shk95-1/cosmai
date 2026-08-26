# 정적 페이지를 nginx 이미지에 그대로 굽는다. 빌드 단계가 없다는 것이 이
# 프로젝트의 성질이다 — 번들러도 트랜스파일러도 없고 public/ 이 곧 브라우저가
# 실행하는 것이다(data-portal/Dockerfile 과 같은 이유, 그 레포를 고치지 않고 다시 씀).
FROM nginx:alpine

# 기본 문서 루트. index.html(지표)·ops.html(관제, #139)·map.html(구조, #142)과 그 셋이 부르는
# js·css 가 평평하게 놓인다('./query.js' import 가 웹 루트 기준으로 풀리는 이유다). 세 페이지는
# style.css 와 query.js 를 공유하되 부팅은 저마다 갈린다 -- 관제만 보려고 열어도 metrics 를 받는
# 일이 없어야 하고, 구조만 보려고 열어도 지표도 관제도 받지 않아야 한다.
COPY public/ /usr/share/nginx/html/

# 이 컨테이너에는 DB 자격증명도 백엔드도 없다 — 브라우저가 PostgREST(3000)를 직접 부른다.
EXPOSE 80
