from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
import os
import json
import jwt
import hashlib
from django.db.models import Max
from datetime import datetime
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
from ..models import Objectinfo, Typeinfo, Rubricinfo, Aspnetusers, Tenant, Objectlinkobject, Composition, Sample,Propertyfloat
from django.utils import timezone
from rest_framework import status


BASE_FILE_PATH = settings.BASE_FILE_PATH  
ACCESS_CONTROL_MAP = {
    'public': 0,
    'protected': 1,
    'protectednda': 2,
    'private': 3
}


def rubric_list(request):
    rubrics = Rubricinfo.objects.all().values('rubricid', 'rubricname', 'rubricnameurl')
    return JsonResponse(list(rubrics), safe=False)


def handle_calculation_sample_or_composition(object_id, elements_data, object_type):
    """
    Create a Sample and, if applicable, related Composition entries based on the object type.

    :param object_id: ID of the created Objectinfo instance.
    :param elements_data: List of elements and their properties (absolute and percentage values).
    :param object_type: The type of the object (e.g., 'Calculation/Computational Composition', 'Composition', 'Request for Synthesis').
    """
    try:
        # Validate and parse the elements data
        element_names = []
        elements_with_no_percentage = []

        for element in elements_data:
            # Ensure all required keys are present
            if 'name' not in element or not element['name']:
                raise ValueError(f"Element data missing required fields: {element}")

            # Track elements with no percentage for applicable types
            if element.get('percentage') is None and object_type in ['Calculation/Computational Composition', 'Composition']:
                elements_with_no_percentage.append(element)

            # Append the element name to the list
            element_names.append(element['name'])

        # Skip percentage validation for specific types
        if object_type not in ['Request for Synthesis', 'Composition Test', 'Calculation/Computational Sample', 'Computational Composition Atom']:
            total_percentage = sum(
                float(element.get('percentage', 0)) for element in elements_data if element.get('percentage') is not None
            )

            if total_percentage > 100:
                raise ValueError(f"Total percentage exceeds 100: {total_percentage}")

            # Distribute remaining percentage evenly among elements with no percentage
            if elements_with_no_percentage:
                remaining_percentage = 100 - total_percentage
                if remaining_percentage < 0:
                    raise ValueError(f"Total percentage exceeds 100: {total_percentage}")
                distributed_percentage = remaining_percentage / len(elements_with_no_percentage)
                for element in elements_with_no_percentage:
                    element['percentage'] = distributed_percentage

            # Ensure the total percentage is exactly 100%
            final_total_percentage = total_percentage + sum(
                element['percentage'] for element in elements_with_no_percentage
            )
            if final_total_percentage != 100:
                raise ValueError(f"Final total percentage does not equal 100: {final_total_percentage}%. Please adjust values.")

        # Create the Sample instance
        sample = Sample(
            sampleid=Objectinfo.objects.get(objectid=object_id),
            elemnumber=len(elements_data),
            elements='-'.join(element_names),  # Save all element names as a hyphen-separated string
        )
        sample.save()
        print(f"Sample created successfully with ID: {sample.sampleid}")

        # Skip Composition creation for specific types
        if object_type in ['Request for Synthesis', 'Composition Test', 'Calculation/Computational Sample', 'Computational Composition Atom']:
            print("Skipping Composition creation for specific types")
            return

        # Create Composition entries for each element
        for idx, element in enumerate(elements_data):
            # Extract and normalize the absolute and percentage values
            value_absolute = float(element['absolute']) if element.get('absolute') is not None else None
            value_percentage = float(element['percentage']) if element.get('percentage') is not None else None

            # Create a Composition entry
            composition = Composition(
                compositionid=(Composition.objects.aggregate(Max('compositionid'))['compositionid__max'] or 0) + 1,
                sampleid=sample,
                compoundindex=idx + 1,
                elementname=element['name'],
                valueabsolute=value_absolute,
                valuepercent=value_percentage,
            )
            composition.save()
            print(f"Composition entry created for element: {element['name']}")

    except Exception as e:
        print(f"Error creating Sample and Composition: {e}")
        raise


@csrf_exempt
def create_object(request):
    if request.method != 'POST':
        return JsonResponse(
            {'error': 'Only POST method is allowed'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED
        )

    try:
        # Extract and validate Authorization token
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return JsonResponse(
                {'error': 'Authorization header missing or malformed'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        token = auth_header.split(' ')[1]
        decoded_token = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        user_id = decoded_token.get('user_id')
        if not user_id:
            return JsonResponse(
                {'error': 'Invalid token: User ID missing'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        # Retrieve fields from the request
        type_name = request.POST.get('typeId')
        tenant_id = request.POST.get('tenantId')
        rubric_id = request.POST.get('rubricId', None)  # Optional
        object_name = request.POST.get('name')
        object_url = request.POST.get('url', f"default-url-{datetime.now().strftime('%Y%m%d%H%M%S')}")
        sort_code = request.POST.get('sortCode', 0)
        description = request.POST.get('description', '')
        linked_object_id = request.POST.get('objectId', None)  # ID of an existing object to link the new object to

        # Validate and retrieve required objects
        type_info = Typeinfo.objects.get(typename=type_name)
        tenant = Tenant.objects.get(tenantid=tenant_id)
        created_by = Aspnetusers.objects.get(id=user_id)
        rubric = Rubricinfo.objects.get(rubricid=rubric_id) if rubric_id else None

        access_control_value = request.POST.get('accessControl', 'public')
        access_control = ACCESS_CONTROL_MAP.get(access_control_value.lower(), 1)

        # Handle file saving and check for duplicates
        file = request.FILES.get('filePath')
        file_path = None
        file_hash = None

        if file:
            # Calculate the file hash
            file_hash = hashlib.md5(file.read()).hexdigest()
            file.seek(0)  # Reset file pointer after reading for hash

            # Check if a file with the same hash already exists
            duplicate_object = Objectinfo.objects.filter(objectfilehash=file_hash).first()
            if duplicate_object:
                # Return details of the existing object
                return JsonResponse(
                    {
                        'error': 'File already exists with the same content.',
                        'existing_object': {
                            'objectId': duplicate_object.objectid,
                            'objectName': duplicate_object.objectname,
                            'createdDate': duplicate_object.field_created,
                            'description': duplicate_object.objectdescription,
                            'url': duplicate_object.objectnameurl,
                        }
                    },
                    status=status.HTTP_409_CONFLICT
                )

            # Save the file to the defined base path if no duplicate found
            file_path = os.path.join(BASE_FILE_PATH, file.name)
            with open(file_path, 'wb') as destination:
                for chunk in file.chunks():
                    destination.write(chunk)

        # Generate a new Object ID
        max_id = Objectinfo.objects.aggregate(Max('objectid'))['objectid__max']
        next_id = (max_id or 0) + 1

        # Create Objectinfo instance
        new_object = Objectinfo(
            objectid=next_id,
            tenantid=tenant,
            field_created=timezone.now(),
            field_createdby=created_by,
            field_updated=timezone.now(),
            field_updatedby=created_by,
            typeid=type_info,
            rubricid=rubric,
            sortcode=int(sort_code),
            accesscontrol=access_control,
            ispublished=False,
            externalid=request.POST.get('externalId'),
            objectname=object_name,
            objectnameurl=object_url,
            objectfilepath=file_path,
            objectfilehash=file_hash,
            objectdescription=description
        )

        # Print data to verify what will be saved
        print("New Object data to be saved:", new_object.__dict__)

        # Save object in the database
        new_object.save()
        print("Object created successfully with ID:", new_object.objectid)

        # Link the new object to an existing object if linked_object_id is provided
        if linked_object_id:
            try:
                existing_object = Objectinfo.objects.get(objectid=linked_object_id)
                
                # Create a new Objectlinkobject for linking
                max_link_id = Objectlinkobject.objects.aggregate(Max('objectlinkobjectid'))['objectlinkobjectid__max']
                new_link_id = (max_link_id or 0) + 1

                link_object = Objectlinkobject(
                    objectlinkobjectid=new_link_id,
                    objectid=  existing_object,
                    linkedobjectid=new_object,
                    sortcode=0,
                    field_created=timezone.now(),
                    field_createdby=created_by,
                    field_updated=timezone.now(),
                    field_updatedby=created_by,
                    linktypeobjectid=None
                )
                
                # Print data to verify the link details
                print("Object Link data to be saved:", link_object.__dict__)
                
                # Save the link object
                link_object.save()
                print("Linked object created successfully with ID:", link_object.objectlinkobjectid)
            except Objectinfo.DoesNotExist:
                return JsonResponse(
                    {'error': 'Invalid objectId: Linked object does not exist'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        if type_name in ['Calculation/Computational Composition', 'Composition', 'Calculation/Computational Sample', 'Composition Test', 'Computational Composition Atom', "Request for Synthesis"]:
            elements_data_raw = request.POST.get('elements', '[]')
            print("Elements data received (raw):", elements_data_raw)

            try:
                elements_data = json.loads(elements_data_raw)
                if type_name in ['Calculation/Computational Composition', 'Composition'] and not elements_data:
                    return JsonResponse(
                        {'error': 'Elements data is required for this type'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                print("Parsed elements data:", elements_data)
                handle_calculation_sample_or_composition(new_object.objectid, elements_data, type_name)
            except json.JSONDecodeError as e:
                return JsonResponse({'error': 'Invalid JSON format for elements data'}, status=400)
            except Exception as e:
                print(f"Error in handle_calculation_sample_or_composition: {e}")
                return JsonResponse({'error': f"Error processing elements data: {str(e)}"}, status=400)
        tolerance = request.POST.get('tolerance')  # Extract tolerance value from the request
        if tolerance:
            try:
                tolerance_value = float(tolerance)  # Ensure it's a valid float
                property_float = Propertyfloat(
                    objectid=new_object,
                    sortcode=1,
                    field_created=timezone.now(),
                    field_createdby=created_by,
                    field_updated=timezone.now(),
                    field_updatedby=created_by,
                    row=None,  # Optional
                    value=tolerance_value,
                    propertyname='Tolerance',
                    comment='at. % -- required',
                )
                property_float.save()
                print(f"Tolerance saved successfully with value: {tolerance_value}")
            except ValueError:
                return JsonResponse({'error': 'Invalid tolerance value'}, status=400)

        return JsonResponse({'message': 'Object created successfully!', 'objectId': new_object.objectid}, status=201)



    except Exception as e:
        print(f"Unexpected error occurred: {e}")
        return JsonResponse({'error': f"Internal Server Error: {str(e)}"}, status=500)