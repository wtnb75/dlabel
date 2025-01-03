FROM python:3-alpine as build
COPY . /app
WORKDIR /app
RUN pip install build
RUN python -m build -w

FROM python:3-alpine
COPY --from=build /app/dist/dlabel*.whl /
RUN ls -l /
RUN pip install /dlabel*.whl paramiko
RUN apk add --no-cache nginx
