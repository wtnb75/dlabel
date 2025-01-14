FROM python:3-alpine AS build
COPY . /app
WORKDIR /app
RUN pip install build
RUN python -m build -w

FROM python:3-alpine
COPY --from=build /app/dist/dlabel*.whl /
RUN --mount=type=cache,mode=0755,target=/root/.cache/pip python -B -m pip install --no-compile /dlabel*.whl paramiko
RUN apk add --no-cache nginx
