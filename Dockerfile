FROM python:3.8
# TODO update to py3.9 when python-ldap (pip) is compatible

LABEL maintainer="Alessio Gerace <a.gerace@inrim.it>"

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV DEBIAN_FRONTEND noninteractive
ARG TZ

RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone


RUN apt-get update;  \
    apt-get upgrade;  \
    apt-get install -y \
            build-essential python3-dev git \
            ldap-utils libldap-dev libsasl2-dev python3-dev  \
            gcc g++ locales locales-all; \
    apt-get clean

ENV LC_ALL it_IT.UTF-8
ENV LANG en_US.UTF-8
ENV LANGUAGE en_US.UTF-8
RUN rm -rf /root/.cache/pip

COPY requirements.txt /requirements.txt
COPY requirements_backend.sh /requirements_backend.sh
RUN chmod +x /requirements_backend.sh



COPY ./start.sh /start.sh
RUN chmod +x /start.sh
COPY ./gunicorn_conf.py /gunicorn_conf.py
VOLUME ["/app"]

RUN ./requirements_backend.sh

WORKDIR /app

ENV PYTHONPATH=/app
EXPOSE 80
CMD ["/start.sh"]

