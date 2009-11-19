"""
Client wrapper for Amazon's Simple Storage Service.

API stability: unstable.

Various API-incompatible changes are planned in order to expose missing
functionality in this wrapper.
"""
import mimetypes

from twisted.web.http import datetimeToString

from epsilon.extime import Time

from txaws.client.base import BaseQuery
from txaws.service import AWSServiceEndpoint, S3_ENDPOINT
from txaws.util import XML, calculate_md5


class S3Client(object):

    def __init__(self, creds=None, endpoint=None, query_factory=None):
        if query_factory is None:
            query_factory = Query
        self.query_factory = query_factory

        self.creds = creds
        self.endpoint = endpoint

    def make_request(self, *a, **kw):
        """
        Create a request with the arguments passed in.

        This uses the request_factory attribute, adding the creds and endpoint
        to the arguments passed in.
        """
        return self.request_factory(creds=self.creds, endpoint=self.endpoint,
                                    *a, **kw)

    def list_buckets(self):
        """
        List all buckets.

        Returns a list of all the buckets owned by the authenticated sender of
        the request.
        """
        query = self.query_factory(action="GET", creds=self.creds,
                                   endpoint=self.endpoint)
        d = query.submit()
        return d.addCallback(self._parse_list_buckets)

    def _parse_list_buckets(self, xml_bytes):
        """
        Parse XML bucket list response.
        """
        root = XML(xml_bytes)
        for bucket in root.find("Buckets"):
            timeText = bucket.findtext("CreationDate")
            yield {
                "name": bucket.findtext("Name"),
                "created": Time.fromISO8601TimeAndDate(timeText),
                }

    def create_bucket(self, bucket):
        """
        Create a new bucket.
        """
        query = self.query_factory("PUT", self.creds, self.endpoint,
                                   bucket=bucket)
        return query.submit()

    def delete_bucket(self, bucket):
        """
        Delete a bucket.

        The bucket must be empty before it can be deleted.
        """
        return self.make_request("DELETE", bucket).submit()

    def put_object(self, bucket, object_name, data, content_type=None,
                   metadata={}):
        """
        Put an object in a bucket.

        Any existing object of the same name will be replaced.
        """
        return self.make_request("PUT", bucket, object_name, data,
                                 content_type, metadata).submit()

    def get_object(self, bucket, object_name):
        """
        Get an object from a bucket.
        """
        return self.make_request("GET", bucket, object_name).submit()

    def head_object(self, bucket, object_name):
        """
        Retrieve object metadata only.

        This is like get_object, but the object's content is not retrieved.
        Currently the metadata is not returned to the caller either, so this
        method is mostly useless, and only provided for completeness.
        """
        return self.make_request("HEAD", bucket, object_name).submit()

    def delete_object(self, bucket, object_name):
        """
        Delete an object from a bucket.

        Once deleted, there is no method to restore or undelete an object.
        """
        return self.make_request("DELETE", bucket, object_name).submit()


class Query(BaseQuery):
    """A query for submission to the S3 service."""

    def __init__(self, bucket=None, object_name=None, data="",
                 content_type=None, metadata={}, *args, **kwargs):
        super(Query, self).__init__(*args, **kwargs)
        self.bucket = bucket
        self.object_name = object_name
        self.data = data
        self.content_type = content_type
        self.metadata = metadata
        self.date = datetimeToString()
        # XXX add unit test
        if not self.endpoint or not self.endpoint.host:
            self.endpoint = AWSServiceEndpoint(S3_ENDPOINT)
        # XXX add unit test
        self.endpoint.set_method(self.action)

    def get_host(self):
        if not self.bucket:
            return self.endpoint.get_host()
        else:
            return "%s.%s" % (self.bucket, self.endpoint.get_host())

    def get_path(self):
        path = "/"
        if self.bucket is not None and self.object_name:
            if self.object_name.startswith("/"):
                path = self.object_name
            else:
                path += self.object_name
        return path

    def get_uri(self):
        return "%s://%s%s" % (
            self.endpoint.scheme, self.get_host(), self.get_path())

    # XXX needs unit tests
    def set_content_type(self):
        if self.object_name and not self.content_type:
            # XXX nothing is currently done with the encoding... we may
            # need to in the future
            self.content_type, encoding = mimetypes.guess_type(
                self.object_name, strict=False)

    def get_headers(self):
        headers = {"Content-Length": len(self.data),
                   "Content-MD5": calculate_md5(self.data),
                   "Date": self.date}
        for key, value in self.metadata.iteritems():
            headers["x-amz-meta-" + key] = value
        # Before we check if the content type is set, let's see if we can set
        # if by guessing the the mimetype.
        self.set_content_type()
        if self.content_type is not None:
            headers["Content-Type"] = self.content_type
        if self.creds is not None:
            signature = self.sign(headers)
            headers["Authorization"] = "AWS %s:%s" % (
                self.creds.access_key, signature)
        return headers

    def get_canonicalized_amz_headers(self, headers):
        result = ""
        headers = [
            (name.lower(), value) for name, value in headers.iteritems()
            if name.lower().startswith("x-amz-")]
        headers.sort()
        # XXX missing spec implementation:
        # 1) txAWS doesn't currently combine headers with the same name
        # 2) txAWS doesn't currently unfold long headers
        return "".join("%s:%s\n" % (name, value) for name, value in headers)

    def sign(self, headers):
        text = (self.action + "\n" +
                headers.get("Content-MD5", "") + "\n" +
                headers.get("Content-Type", "") + "\n" +
                headers.get("Date", "") + "\n" +
                self.get_canonicalized_amz_headers(headers) +
                self.get_path())
        return self.creds.sign(text, hash_type="sha1")

    def submit(self):
        d = self.get_page(
            self.get_uri(), method=self.action, postdata=self.data,
            headers=self.get_headers())
        # XXX - we need an error wrapper like we have for ec2... but let's wait
        # until the new error-wrapper brach has landed, and possibly generalize
        # a base class for all clients.
        #d.addErrback(s3_error_wrapper)
        return d
