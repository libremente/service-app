# Copyright INRIM (https://www.inrim.eu)
# See LICENSE file for full licensing details.
import json
import sys
from typing import Optional

from fastapi import FastAPI, Request, Header, HTTPException, Depends
from fastapi.responses import RedirectResponse, FileResponse
import httpx
import logging
import ujson
from datetime import datetime, timedelta
from fastapi.concurrency import run_in_threadpool
from aiopath import AsyncPath
from .main.base.base_class import PluginBase
from .ContentService import ContentService
import aiofiles
import uuid

logger = logging.getLogger(__name__)


class ProcessService(PluginBase):
    plugins = []

    def __init_subclass__(cls, **kwargs):
        if cls not in cls.plugins:
            cls.plugins.append(cls())


class ProcessServiceBase(ProcessService):

    @classmethod
    def create(cls, content_service: ContentService, process_model, process_name):
        self = ProcessServiceBase()
        self.init(content_service, process_model, process_name)
        return self

    def init(self, content_service: ContentService, process_model, process_name):
        self.content_service = content_service
        self.gateway = content_service.gateway
        self.process_model = process_model
        self.process_rec_name = process_name
        self.cfg = {}
        self.process_instance_id = ""
        self.process_data = {}
        self.process = {}
        self.process_tasks = {}
        self.current_task = {}

    async def load_config(self):
        self.process_data = await self.gateway.get_record_data(
            self.process_model, self.process_rec_name)
        self.cfg = await self.gateway.get_param(self.process_data.get("model"))

    async def start(self, **kwargs):
        await self.load_config()
        self.process = {}
        return self.process

    async def next(self, **kwargs):
        await self.load_config()
        self.process = {}
        return self.process

    async def complete(self, **kwargs):
        await self.load_config()
        self.process = {}
        return self.process

    async def cancel(self, **kwargs):
        await self.load_config()
        process = {}
        return process
