"""
Object-based interface to the database used by the chatbot.

The database is accessed through the Database object:

>>> db = Database("sqlite:///:memory:")
>>> db.create_database_schema()

(Note: for now, sqlite is the only supported database; support for other
databases will require adding string length constraints to some of the database
columns.)

Recipes are added to the database using dictionaries.  The allrecipes.py file
contains code to generate dictionaries from scraped recipes.  For testing, you
can create them by hand:

>>> recipe_parts = {
... 'title' :  'Peanut butter and jelly sandwich',
... 'author' : 'Anonymous',
... 'url' : 'http://example.com/pbj.html',
... 'prep_time' : 5,
... 'total_time' : 5,
... 'servings' : 1,
... 'ingredients' : [
...     '1 cup peanut butter',
...     '1 tablespoon jelly',
...     '2 slices sliced bread'],
... 'steps' : [
...     'Remove bread from package',
...     'Spread peanut butter and jelly onto each slice of bread.',
...     'Combine slices of bread, optionally remove crust, and eat.']
... }
>>> db.add_from_recipe_parts(recipe_parts)

You can search for recipes based on the ingredients that they contain:

>>> recipes = db.get_recipes(include_ingredients=['spam'])
>>> len(recipes)
0
>>> recipes = db.get_recipes(include_ingredients=['peanut butter'],
...                          exclude_ingredients=['chicken'])
>>> len(recipes)
1

The results of the query are returned as a list of Recipe objects.  You can
access a recipe's ingredients through its ingredients attribute, which is
a list of RecipeIngredientAssociation objects.  Each object represents a line
containing an ingredient in the recipe.

>>> recipes[0].ingredients[0].ingredient.name
u'peanut butter'
>>> recipes[0].ingredients[0].unit
u'cup'
>>> recipes[0].ingredients[0].quantity
u'1'
>>> recipes[0].ingredients[2].modifiers
u'sliced'

The RecipeIngredientAssociation objects can be printed:

>>> for ingredient in recipes[0].ingredients:
...     print ingredient
1 cup peanut butter
1 tablespoon jelly
2 slices sliced bread

>>> print recipes[0].steps_text
Remove bread from package
Spread peanut butter and jelly onto each slice of bread.
Combine slices of bread, optionally remove crust, and eat.

You can construct some very complicated queries:

>>> recipes = db.get_recipes(include_ingredients=['bacon', 'chocolate'],
...     exclude_ingredients=['blueberries'], prep_time=5, cook_time=(None, 20),
...     total_time=(10, 30), num_steps=(3, None))

For the full details on the search capabilities, see the documentation for the
get_recipes() method.
"""
from collections import defaultdict

from sqlalchemy import create_engine, Table, Column, Integer, \
    String, ForeignKey
from sqlalchemy.sql.expression import between
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker, join

from nlu import extract_ingredient_parts, normalize_ingredient_name


Base = declarative_base()


recipe_categories = Table('recipe_categories', Base.metadata,
    Column('recipe_id', Integer, ForeignKey('recipes.id')),
    Column('category_id', Integer, ForeignKey('categories.id'))
)


class Database(object):
    """
    Represents a connection to a specific database and provides convenience
    methods for obtaining objects from that database.
    """

    def __init__(self, database_url):
        """
        Connect to a database, specified by a database URL.
        For the URL format, see
        http://www.sqlalchemy.org/docs/core/engines.html#database-urls
        """
        self._engine = create_engine(database_url)
        self._sessionmaker = sessionmaker(bind=self._engine)
        self._session = self._sessionmaker()
        self.create_database_schema()

    def create_database_schema(self):
        """
        If necessary, creates the tables in the database.
        """
        Base.metadata.create_all(self._engine)

    def add_from_recipe_parts(self, recipe_parts):
        """
        Add a recipe from a dictionary describing the recipe.  The dictionary
        could be generated by a scraper.  For an example, see the
        extract_recipe_parts function in allrecipes.py.

        Raises a DuplicateRecipeException when inserting a duplicate recipe.
        """
        # First, make sure that we're not inserting a duplicate record.
        # Duplicates are considered to be recipes with the same url.
        duplicate = self._session.query(Recipe).\
                        filter_by(url=recipe_parts['url'])
        if duplicate.first():
            raise DuplicateRecipeException(
                "Recipe with url %s already exists." % recipe_parts['url'])
        recipe = Recipe()
        recipe_parts = defaultdict(str, recipe_parts)
        recipe.title = recipe_parts['title']
        recipe.url = recipe_parts['url']
        recipe.author = recipe_parts['author']
        recipe.description = recipe_parts['description']
        recipe.num_steps = recipe_parts['num_steps']
        recipe.servings = recipe_parts['servings']
        recipe.prep_time = recipe_parts['prep_time']
        recipe.cook_time = recipe_parts['cook_time']
        recipe.total_time = recipe_parts['total_time']
        recipe.ingredients_text = "\n".join(recipe_parts['ingredients'])
        recipe.steps_text = "\n".join(recipe_parts['steps'])

        for ingredient_string in recipe_parts['ingredients']:
            ingredient_parts = extract_ingredient_parts(ingredient_string)
            if not ingredient_parts:
                continue
            ingredient_parts = defaultdict(lambda: None, ingredient_parts)
            ingredient = self._session.query(Ingredient).filter_by(
                name=ingredient_parts['base_ingredient']).first()
            if not ingredient:
                ingredient = Ingredient(ingredient_parts['base_ingredient'])
                self._session.add(ingredient)
                self._session.flush()
            unit = ingredient_parts['unit']
            quantity = ingredient_parts['quantity']
            modifiers = ingredient_parts['modifiers']
            assoc = RecipeIngredientAssociation(ingredient, unit, quantity,
                                                modifiers)
            recipe.ingredients.append(assoc)
        self._session.add(recipe)
        self._session.commit()

    def get_recipes(self, include_ingredients=(), exclude_ingredients=(),
                    prep_time=None, cook_time=None, total_time=None,
                    num_steps=None):
        """
        Get recipes matching the given criteria.

        Numeric attributes, like total_time, can be specified as single values
        (to retreive exact matches) or (min, max) tuples that define ranges
        which include their endpoints.  To specify just a maximum or minimum,
        set the other value to None.

        For example, to find recipes with a total time of 1/2 to 1 hours:
        >>> db = Database("sqlite:///:memory:")
        >>> recipes = db.get_recipes(total_time=(30, 60))

        Or, to find recipes that take up to 15 minutes to prepare:
        >>> recipes = db.get_recipes(prep_time=(None, 15))

        To find recipes that have exactly 5 steps:
        >>> recipes = db.get_recipes(num_steps=5)
        """
        # Normalize ingredient names, so that they match the names stored in
        # the database.
        include_ingredients = \
            (normalize_ingredient_name(i) for i in include_ingredients)
        exclude_ingredients = \
            (normalize_ingredient_name(i) for i in exclude_ingredients)
        # Construct the query
        query = self._session.query(Recipe)
        # Handle ingredient inclusion and exclusion
        if include_ingredients or exclude_ingredients:
            double_join = join(RecipeIngredientAssociation, Recipe)
            triple_join = join(double_join, Ingredient)
            query = query.select_from(triple_join)
            for ingredient_name in include_ingredients:
                query = query.filter(Ingredient.name == ingredient_name)
            for ingredient_name in exclude_ingredients:
                query = query.filter(Ingredient.name != ingredient_name)
        # Handle ranges searches over simple numeric attributes, like
        # total_time or num_steps
        if total_time != None:
            query = query.filter(_range_predicate(Recipe.total_time,
                total_time))
        if cook_time != None:
            query = query.filter(_range_predicate(Recipe.cook_time, cook_time))
        if prep_time != None:
            query = query.filter(_range_predicate(Recipe.prep_time, prep_time))
        if num_steps != None:
            query = query.filter(_range_predicate(Recipe.num_steps, num_steps))
        return query.all()


class RecipeIngredientAssociation(Base):
    """
    Associates an ingredient with a recipe.  Contains information about the
    association, such as the amount of the ingredient or modifiers (such as
    'chopped' or 'fresh').
    """
    __tablename__ = 'recipe_ingredient_association'
    # These primary key constraints allow a recipe to list the same ingredient
    # twice, e.g. 'chopped apples' and 'pureed apples' as separate ingredients.
    recipe_ingredient_association_id = Column(Integer, primary_key=True)
    recipe_id = Column(Integer, ForeignKey('recipes.id'))
    recipe = relationship("Recipe")
    ingredient_id = Column(Integer, ForeignKey('ingredients.id'))
    ingredient = relationship("Ingredient")
    quantity = Column(String)
    unit = Column(String)
    modifiers = Column(String)

    def __init__(self, ingredient, unit, quantity, modifiers):
        self.ingredient = ingredient
        self.unit = unit
        self.quantity = quantity
        self.modifiers = modifiers

    def __repr__(self):
        return "<RecipeIngredientAssociation(%s, %s)>" % \
            (self.recipe.title, self.ingredient.name)

    def __str__(self):
        parts = [self.quantity, self.unit, self.modifiers,
            self.ingredient.name]
        return ' '.join(x for x in parts if x)


class Recipe(Base):
    """
    Represents a single recipe.
    """
    __tablename__ = 'recipes'

    id = Column(Integer, primary_key=True)
    url = Column(String, unique=True)
    title = Column(String, nullable=False)
    author = Column(String)
    description = Column(String)
    ingredients = relationship(RecipeIngredientAssociation)
    categories = relationship('Category', secondary=recipe_categories,
                               backref='recipes')
    num_steps = Column(Integer)
    ingredients_text = Column(String)
    steps_text = Column(String)
    servings = Column(String)
    prep_time = Column(Integer)
    cook_time = Column(Integer)
    total_time = Column(Integer)

    def __init__(self, title=None):
        self.title = title

    def __repr__(self):
        return "<Recipe(%s)>" % self.title


class Ingredient(Base):
    """
    Represents a single ingredient as the food item itself, not a quantity of a
    prepared or modified ingredient.  For example, Ingredient can represent an
    apple, but not 3/4 cup of finely chopped apples.
    """
    __tablename__ = 'ingredients'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return "<Ingredient(%s)>" % self.name


class Category(Base):
    """
    Represents a category that a recipe can belong to, like Breakfast or
    Indian.
    """
    __tablename__ = 'categories'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return "<Category(%s)>" % self.name


class DatabaseException(Exception):
    """
    Base class for exceptions thrown by the database.
    """
    pass


class DuplicateRecipeException(DatabaseException):
    """
    Thrown when trying to insert a duplicate recipe into the database.
    """
    pass


def _range_predicate(attribute, val_range):
    """
    Accepts an attribute and a tuple (min, max), and returns a predicate to
    find items whose attribute values fall within that range.  The range
    includes the endpoints.

    This is a private helper function used to avoid cluttering get_recipes().
    """
    if not hasattr(val_range, '__iter__'):
        return attribute == val_range
    else:
        if len(val_range) != 2:
            raise ValueError(
                "Invalid range %s; valid ranges are (min, max) tuples."
                % str(val_range))
        (min_val, max_val) = val_range
        if min_val != None and max_val != None:
            return between(attribute, min_val, max_val)
        elif min_val != None:
            return attribute >= min_val
        else:
            return attribute <= max_val
