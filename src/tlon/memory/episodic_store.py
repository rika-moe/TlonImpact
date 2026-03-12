"""Episodic Memory store implementation using JSONL files."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles

from ..logger import get_logger
from .base import MemoryStore

logger = get_logger(__name__)


class EpisodicMemoryStore(MemoryStore):
    """Episodic Memory store for summaries and facts.
    
    Stores long-term memory in two categories:
    - Summaries: Session summaries generated from working memory
    - Facts: Key facts extracted from interactions
    
    Storage format: JSON Lines (JSONL), one item per line
    
    Attributes:
        agent_id: The agent this store belongs to
        storage_path: Path to the storage directory
        max_summaries: Maximum summaries to keep
        max_facts: Maximum facts to keep
    
    Example:
        >>> store = EpisodicMemoryStore("dev-001", Path("./data/agents/dev-001/memory"))
        >>> await store.load()
        >>> summary_id = await store.add_summary(summary_dict)
        >>> fact_id = await store.add_fact(fact_dict)
        >>> recent_summaries = await store.get_recent_summaries(limit=5)
    """
    
    def __init__(
        self, 
        agent_id: str, 
        storage_path: Path,
        max_summaries: int = 20,
        max_facts: int = 50
    ):
        """Initialize Episodic Memory store.
        
        Args:
            agent_id: The agent ID
            storage_path: Directory for storage
            max_summaries: Maximum summaries to keep (default: 20)
            max_facts: Maximum facts to keep (default: 50)
        """
        super().__init__(agent_id)
        self.storage_path = storage_path
        self.episodic_dir = storage_path / "episodic"
        self.summaries_file = self.episodic_dir / "summaries.jsonl"
        self.facts_file = self.episodic_dir / "facts.jsonl"
        self.max_summaries = max_summaries
        self.max_facts = max_facts
        self._summaries_cache: List[Dict[str, Any]] = []
        self._facts_cache: List[Dict[str, Any]] = []
        self._loaded = False
    
    async def _ensure_dir(self) -> None:
        """Ensure storage directory exists."""
        self.episodic_dir.mkdir(parents=True, exist_ok=True)
    
    async def load(self) -> None:
        """Load summaries and facts from disk.
        
        Should be called once at startup.
        """
        await self._ensure_dir()
        
        # Load summaries
        if self.summaries_file.exists():
            try:
                self._summaries_cache = []
                async with aiofiles.open(self.summaries_file, 'r', encoding='utf-8') as f:
                    async for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            self._summaries_cache.append(data)
                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse summary line: {e}")
                
                # Trim if exceeds max
                if len(self._summaries_cache) > self.max_summaries:
                    self._summaries_cache = self._summaries_cache[-self.max_summaries:]
                    await self._persist_summaries()
                
                logger.info(f"Loaded {len(self._summaries_cache)} summaries for {self.agent_id}")
            except Exception as e:
                logger.error(f"Error loading summaries: {e}")
                self._summaries_cache = []
        else:
            logger.info(f"No existing summaries for {self.agent_id}")
            self._summaries_cache = []
        
        # Load facts
        if self.facts_file.exists():
            try:
                self._facts_cache = []
                async with aiofiles.open(self.facts_file, 'r', encoding='utf-8') as f:
                    async for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            self._facts_cache.append(data)
                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse fact line: {e}")
                
                # Trim if exceeds max
                if len(self._facts_cache) > self.max_facts:
                    self._facts_cache = self._facts_cache[-self.max_facts:]
                    await self._persist_facts()
                
                logger.info(f"Loaded {len(self._facts_cache)} facts for {self.agent_id}")
            except Exception as e:
                logger.error(f"Error loading facts: {e}")
                self._facts_cache = []
        else:
            logger.info(f"No existing facts for {self.agent_id}")
            self._facts_cache = []
        
        self._loaded = True
    
    async def _persist_summaries(self) -> None:
        """Write summaries to disk."""
        try:
            async with aiofiles.open(self.summaries_file, 'w', encoding='utf-8') as f:
                for item in self._summaries_cache:
                    await f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Error persisting summaries: {e}")
    
    async def _persist_facts(self) -> None:
        """Write facts to disk."""
        try:
            async with aiofiles.open(self.facts_file, 'w', encoding='utf-8') as f:
                for item in self._facts_cache:
                    await f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Error persisting facts: {e}")
    
    # ===== Summary Operations =====
    
    async def add_summary(self, summary: Dict[str, Any]) -> str:
        """Add a session summary.
        
        Args:
            summary: The summary dictionary (must have 'id' field)
            
        Returns:
            The summary ID
        """
        if not self._loaded:
            await self.load()
        
        summary_id = summary.get("id")
        if not summary_id:
            raise ValueError("Summary must have an 'id' field")
        
        # Add to cache
        self._summaries_cache.append(summary)
        
        # Trim if exceeds max
        if len(self._summaries_cache) > self.max_summaries:
            removed = self._summaries_cache[:-self.max_summaries]
            self._summaries_cache = self._summaries_cache[-self.max_summaries:]
            logger.debug(f"Trimmed {len(removed)} old summaries")
        
        # Append to file
        await self._ensure_dir()
        try:
            async with aiofiles.open(self.summaries_file, 'a', encoding='utf-8') as f:
                await f.write(json.dumps(summary, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Error appending summary: {e}")
            await self._persist_summaries()
        
        return summary_id
    
    async def get_summary(self, summary_id: str) -> Optional[Dict[str, Any]]:
        """Get a summary by ID."""
        if not self._loaded:
            await self.load()
        
        for item in self._summaries_cache:
            if item.get("id") == summary_id:
                return item
        return None
    
    async def get_recent_summaries(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get recent summaries.
        
        Args:
            limit: Maximum number to return
            
        Returns:
            List of summaries, newest first
        """
        if not self._loaded:
            await self.load()
        
        return self._summaries_cache[-limit:] if limit < len(self._summaries_cache) else self._summaries_cache.copy()
    
    async def get_all_summaries(self) -> List[Dict[str, Any]]:
        """Get all summaries."""
        if not self._loaded:
            await self.load()
        
        return self._summaries_cache.copy()
    
    async def delete_summary(self, summary_id: str) -> bool:
        """Delete a summary by ID."""
        if not self._loaded:
            await self.load()
        
        for i, item in enumerate(self._summaries_cache):
            if item.get("id") == summary_id:
                self._summaries_cache.pop(i)
                await self._persist_summaries()
                return True
        return False
    
    # ===== Fact Operations =====
    
    async def add_fact(self, fact: Dict[str, Any]) -> str:
        """Add a key fact.
        
        Args:
            fact: The fact dictionary (must have 'id' field)
            
        Returns:
            The fact ID
        """
        if not self._loaded:
            await self.load()
        
        fact_id = fact.get("id")
        if not fact_id:
            raise ValueError("Fact must have an 'id' field")
        
        # Check for existing fact with same subject-predicate-object
        # If found, mark it as superseded
        subject = fact.get("subject")
        predicate = fact.get("predicate")
        obj = fact.get("object")
        
        for existing in self._facts_cache:
            if (
                existing.get("subject") == subject
                and existing.get("predicate") == predicate
                and existing.get("object") == obj
            ):
                # Same fact, update version
                existing["superseded_by"] = fact_id
                logger.debug(f"Fact superseded: {existing.get('id')} -> {fact_id}")
                break
        
        # Add to cache
        self._facts_cache.append(fact)
        
        # Trim if exceeds max (remove oldest non-superseded facts)
        if len(self._facts_cache) > self.max_facts:
            # Keep facts that are not superseded, prefer newer ones
            non_superseded = [f for f in self._facts_cache if not f.get("superseded_by")]
            superseded = [f for f in self._facts_cache if f.get("superseded_by")]
            
            # Keep as many non-superseded as possible
            if len(non_superseded) > self.max_facts:
                non_superseded = non_superseded[-self.max_facts:]
            
            self._facts_cache = superseded + non_superseded
            self._facts_cache = self._facts_cache[-self.max_facts:]
        
        # Append to file
        await self._ensure_dir()
        try:
            async with aiofiles.open(self.facts_file, 'a', encoding='utf-8') as f:
                await f.write(json.dumps(fact, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Error appending fact: {e}")
            await self._persist_facts()
        
        return fact_id
    
    async def get_fact(self, fact_id: str) -> Optional[Dict[str, Any]]:
        """Get a fact by ID."""
        if not self._loaded:
            await self.load()
        
        for item in self._facts_cache:
            if item.get("id") == fact_id:
                return item
        return None
    
    async def get_recent_facts(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent facts (excluding superseded ones by default)."""
        if not self._loaded:
            await self.load()
        
        # Filter out superseded facts
        active_facts = [f for f in self._facts_cache if not f.get("superseded_by")]
        return active_facts[-limit:] if limit < len(active_facts) else active_facts.copy()
    
    async def get_all_facts(self, include_superseded: bool = False) -> List[Dict[str, Any]]:
        """Get all facts."""
        if not self._loaded:
            await self.load()
        
        if include_superseded:
            return self._facts_cache.copy()
        return [f for f in self._facts_cache if not f.get("superseded_by")]
    
    async def delete_fact(self, fact_id: str) -> bool:
        """Delete a fact by ID."""
        if not self._loaded:
            await self.load()
        
        for i, item in enumerate(self._facts_cache):
            if item.get("id") == fact_id:
                self._facts_cache.pop(i)
                await self._persist_facts()
                return True
        return False
    
    # ===== Base Class Implementation =====
    
    async def add(self, item: Dict[str, Any]) -> str:
        """Add item - delegates to appropriate method based on type.
        
        Args:
            item: Must have '_type' field: 'summary' or 'fact'
        """
        item_type = item.get("_type", "summary")
        if item_type == "fact":
            return await self.add_fact(item)
        else:
            return await self.add_summary(item)
    
    async def get(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Get item by ID - checks both summaries and facts."""
        result = await self.get_summary(item_id)
        if result:
            return result
        return await self.get_fact(item_id)
    
    async def get_recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent items - returns summaries by default."""
        return await self.get_recent_summaries(limit)
    
    async def delete(self, item_id: str) -> bool:
        """Delete item by ID."""
        if await self.delete_summary(item_id):
            return True
        return await self.delete_fact(item_id)
    
    async def clear(self) -> None:
        """Clear all episodic memory."""
        self._summaries_cache = []
        self._facts_cache = []
        
        if self.summaries_file.exists():
            try:
                self.summaries_file.unlink()
            except Exception as e:
                logger.error(f"Error clearing summaries file: {e}")
        
        if self.facts_file.exists():
            try:
                self.facts_file.unlink()
            except Exception as e:
                logger.error(f"Error clearing facts file: {e}")
    
    async def clear_summaries(self) -> None:
        """Clear only summaries."""
        self._summaries_cache = []
        if self.summaries_file.exists():
            try:
                self.summaries_file.unlink()
            except Exception as e:
                logger.error(f"Error clearing summaries file: {e}")
    
    async def clear_facts(self) -> None:
        """Clear only facts."""
        self._facts_cache = []
        if self.facts_file.exists():
            try:
                self.facts_file.unlink()
            except Exception as e:
                logger.error(f"Error clearing facts file: {e}")
    
    async def count(self) -> int:
        """Get total count of summaries and facts."""
        if not self._loaded:
            await self.load()
        
        return len(self._summaries_cache) + len(self._facts_cache)
    
    async def count_summaries(self) -> int:
        """Get count of summaries."""
        if not self._loaded:
            await self.load()
        
        return len(self._summaries_cache)
    
    async def count_facts(self) -> int:
        """Get count of facts."""
        if not self._loaded:
            await self.load()
        
        return len(self._facts_cache)
    
    # ===== Search =====
    
    async def search_summaries_by_keyword(
        self, 
        keywords: List[str], 
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Search summaries by keywords."""
        if not self._loaded:
            await self.load()
        
        results = []
        for item in reversed(self._summaries_cache):  # Most recent first
            item_keywords = item.get("keywords", [])
            summary_text = item.get("summary_text", "")
            
            # Check keywords list
            if any(kw.lower() in [ik.lower() for ik in item_keywords] for kw in keywords):
                results.append(item)
            # Check summary text
            elif any(kw.lower() in summary_text.lower() for kw in keywords):
                results.append(item)
            
            if len(results) >= limit:
                break
        
        return results
    
    async def search_facts_by_keyword(
        self, 
        keywords: List[str], 
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Search facts by keywords."""
        if not self._loaded:
            await self.load()
        
        results = []
        for item in reversed(self._facts_cache):
            if item.get("superseded_by"):
                continue
            
            description = item.get("description", "")
            subject = item.get("subject", "")
            obj = item.get("object", "")
            
            text = f"{subject} {item.get('predicate', '')} {obj} {description}"
            
            if any(kw.lower() in text.lower() for kw in keywords):
                results.append(item)
            
            if len(results) >= limit:
                break
        
        return results
