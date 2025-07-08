from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from enum import Enum

class Category(str, Enum):
    FOOD_DRINKS = "Food & Drinks"
    TRANSPORT = "Transport"
    SHOPPING = "Shopping"
    GROCERIES = "Groceries"
    BILLS_UTILITIES = "Bills & Utilities"
    ENTERTAINMENT = "Entertainment"
    HEALTH = "Health"
    SERVICES = "Services"
    OTHER = "Other"
    UNCATEGORIZED = "Uncategorized"

class Currency(str, Enum):
    SGD = "SGD"
    USD = "USD"
    EUR = "EUR"

class TransactionSource(str, Enum):
    TEXT = "text"
    IMAGE = "image"

class TimeFrame(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    ALL = "all"

class FilterType(str, Enum):
    CATEGORY = "category"
    KEYWORDS = "keywords"
    NONE = "none"

@dataclass
class Transaction:
    """Transaction data model."""
    amount: float
    currency: Currency = Currency.SGD
    keywords: List[str] = field(default_factory=list)
    category: Category = Category.UNCATEGORIZED
    raw_text: str = ""
    source: TransactionSource = TransactionSource.TEXT
    image_url: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    id: Optional[str] = None
    
    def add_keywords(self, new_keywords: List[str]) -> None:
        """Add new keywords to the transaction."""
        for keyword in new_keywords:
            if keyword not in self.keywords:
                self.keywords.append(keyword)
    
    def get_first_keyword(self) -> Optional[str]:
        """Get the first keyword (usually the merchant name)."""
        return self.keywords[0] if self.keywords else None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for database storage."""
        data = {
            "amount": self.amount,
            "currency": self.currency.value,
            "keywords": self.keywords,
            "category": self.category.value,
            "rawText": self.raw_text,
            "source": self.source.value,
            "imageUrl": self.image_url,
            "createdAt": self.created_at
        }
        if self.id:
            data["_id"] = self.id
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Transaction':
        """Create Transaction from dictionary."""
        if '_id' in data:
            data['id'] = str(data.pop('_id'))
        
        # Handle both new and old database schemas
        if 'parsedData' in data:
            # Old schema
            parsed_data = data.get('parsedData', {})
            return cls(
                id=data.get('id'),
                amount=parsed_data.get('amount', 0),
                currency=Currency(parsed_data.get('currency', 'SGD')),
                keywords=parsed_data.get('keywords', []),
                category=Category(data.get('category', 'Uncategorized')),
                raw_text=data.get('rawText', ''),
                source=TransactionSource(data.get('source', 'text')),
                image_url=data.get('imageUrl'),
                created_at=data.get('createdAt', datetime.now())
            )
        else:
            # New schema
            return cls(
                id=data.get('id'),
                amount=data.get('amount', 0),
                currency=Currency(data.get('currency', 'SGD')),
                keywords=data.get('keywords', []),
                category=Category(data.get('category', 'Uncategorized')),
                raw_text=data.get('rawText', ''),
                source=TransactionSource(data.get('source', 'text')),
                image_url=data.get('imageUrl'),
                created_at=data.get('createdAt', datetime.now())
            ) 