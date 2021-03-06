#!/usr/bin/env python
"""
Simple WSGI application that provides web-based chat interface.

For a demo, run this program and point your browser to
http://localhost:8080
"""

import os
import logging
import uuid
import urlparse
import gc

from chatbot import Chatbot
from database import Database, Base


class WebChatServer(object):
    """
    Provides web-based chat interface.  Instances of this class are
    callable WSGI applications.

    Note: in a real server, you would want sessions to expire after some amount
    of inactivity.
    """

    def __init__(self, db, logger):
        """
        Create a new WSGI application providing a web-based chat
        interface to the given chatbot.
        """
        self.state_datastore = {}
        self.db = db
        self.logger = logger

    def _save_chatbot(self, chatbot, session_id):
        """
        Store the chatbot in the key-value store.
        """
        self.state_datastore[session_id] = chatbot

    def _load_chatbot(self, session_id):
        """
        Load the chatbot from the key-value store.
        """
        return self.state_datastore[session_id]

    def __call__(self, environ, start_response):
        """
        WSGI application implementing a simple chat protocol.
        On a GET request, serve the chat interface.  On a POST, pass
        the input to the chatbot and return its response as text.
        """
        method = environ['REQUEST_METHOD']
        if method == 'GET':
            session_id = str(uuid.uuid4())
            # Start a new conversation
            chatbot = Chatbot(self.db, self.logger, enable_debug=False)
            greeting = chatbot.get_greeting()
            # Save the chatbot in the key-value store
            self._save_chatbot(chatbot, session_id)
            # Return HTML of chat interface
            start_response('200 OK', [('content-type', 'text/html')])
            template = open(os.path.join(os.path.dirname(__file__),
                'chat_interface.html')).read()
            template = template.replace("{{SESSION_ID}}", session_id)
            template = template.replace("{{OUTPUT}}", greeting)
            return (template, )
        elif method == 'POST':
            # Get form data
            length = int(environ.get('CONTENT_LENGTH', '0'))
            post = urlparse.parse_qs(environ['wsgi.input'].read(length))
            session_id = post['session_id'][0]
            chat_message = post['chat_message'][0]
            # Load the saved state
            chatbot = self._load_chatbot(session_id)
            # Get the bot's output
            output = chatbot.handle_input(chat_message)
            # Save the chatbot in the key-value store
            self._save_chatbot(chatbot, session_id)
            # Return the output as text
            start_response('200 OK', [('content-type', 'text/plain')])
            return (output, )


def demo():
    """
    Demo of how to use the WebChatServer WSGI application, using CherryPy's
    WSGI server.
    """
    from cherrypy import wsgiserver
    db = Database('sqlite:///test_database.sqlite')
    logger = logging.getLogger('chatbot_server')
    logging.basicConfig(level=logging.DEBUG)
    chat_app = WebChatServer(db, logger)
    server = wsgiserver.CherryPyWSGIServer(('0.0.0.0', 8080), chat_app)
    try:
        print "Started chatbot web server."
        server.start()
    except KeyboardInterrupt:
        server.stop()
        exit()

if __name__ == "__main__":
    demo()
