from fastapi import APIRouter, HTTPException
from typing import List
from pydantic import BaseModel
from app.database.repositories import ConfigRepository

router = APIRouter(prefix="/api/config", tags=["config"])

class ConfigItemCreate(BaseModel):
    category: str
    config_key: str
    config_value: str

class ConfigItemUpdate(BaseModel):
    config_key: str
    config_value: str

@router.get("")
async def get_all_configs():
    """Get all config items"""
    try:
        repo = ConfigRepository()
        configs = repo.get_all_configs()
        
        # Group by category
        grouped = {}
        for config in configs:
            category = config['category']
            if category not in grouped:
                grouped[category] = []
            grouped[category].append(config)
        
        return grouped
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{category}")
async def get_configs_by_category(category: str):
    """Get config items for a specific category"""
    try:
        repo = ConfigRepository()
        return repo.get_configs_by_category(category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("")
async def create_config(item: ConfigItemCreate):
    """Create a new config item"""
    try:
        repo = ConfigRepository()
        config_id = repo.create_config(
            item.category,
            item.config_key,
            item.config_value
        )
        return {"id": config_id, "message": "Config created successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{config_id}")
async def update_config(config_id: int, item: ConfigItemUpdate):
    """Update a config item"""
    try:
        repo = ConfigRepository()
        success = repo.update_config(
            config_id,
            item.config_key,
            item.config_value
        )
        if success:
            return {"message": "Config updated successfully"}
        else:
            raise HTTPException(status_code=404, detail="Config not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{config_id}")
async def delete_config(config_id: int):
    """Delete a config item"""
    try:
        repo = ConfigRepository()
        success = repo.delete_config(config_id)
        if success:
            return {"message": "Config deleted successfully"}
        else:
            raise HTTPException(status_code=404, detail="Config not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
