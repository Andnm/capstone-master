from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.database.repositories import MarketClusterRepository

router = APIRouter()
market_cluster_repo = MarketClusterRepository()


class MarketClusterMapping(BaseModel):
    code: str
    market: str
    cluster: str


class MarketClusterMappingResponse(MarketClusterMapping):
    id: int


@router.get("/api/market-cluster", response_model=List[MarketClusterMappingResponse])
async def get_all_mappings(limit: int = 1000, offset: int = 0):
    """Get all market-cluster mappings with pagination"""
    try:
        mappings = market_cluster_repo.get_all_mappings(limit, offset)
        return mappings
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/market-cluster/{code}", response_model=MarketClusterMappingResponse)
async def get_mapping_by_code(code: str):
    """Get mapping by code"""
    try:
        mapping = market_cluster_repo.get_mapping_by_code(code)
        if not mapping:
            raise HTTPException(status_code=404, detail="Mapping not found")
        return mapping
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/market-cluster", response_model=dict)
async def create_mapping(mapping: MarketClusterMapping):
    """Create a new market-cluster mapping"""
    try:
        # Check if code already exists
        existing = market_cluster_repo.get_mapping_by_code(mapping.code)
        if existing:
            raise HTTPException(status_code=400, detail="Code already exists")
        
        mapping_id = market_cluster_repo.create_mapping(mapping.dict())
        return {"id": mapping_id, "message": "Mapping created successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/market-cluster/{code}", response_model=dict)
async def update_mapping(code: str, mapping: MarketClusterMapping):
    """Update an existing mapping"""
    try:
        # Verify mapping exists
        existing = market_cluster_repo.get_mapping_by_code(code)
        if not existing:
            raise HTTPException(status_code=404, detail="Mapping not found")
        
        success = market_cluster_repo.update_mapping(code, mapping.dict())
        if success:
            return {"message": "Mapping updated successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to update mapping")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/market-cluster/{code}", response_model=dict)
async def delete_mapping(code: str):
    """Delete a mapping"""
    try:
        success = market_cluster_repo.delete_mapping(code)
        if success:
            return {"message": "Mapping deleted successfully"}
        else:
            raise HTTPException(status_code=404, detail="Mapping not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
