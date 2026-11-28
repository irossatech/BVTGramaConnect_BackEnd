from django.contrib import admin

from .models import District, Mandal, State, Village


class AreaAdmin(admin.ModelAdmin):
    search_fields = ("name_en", "name_te", "lgd_code")
    list_filter = ("is_active",)
    readonly_fields = ("name_key", "created_at", "updated_at")


@admin.register(State)
class StateAdmin(AreaAdmin):
    list_display = ("name_en", "name_te", "is_active")


@admin.register(District)
class DistrictAdmin(AreaAdmin):
    list_display = ("name_en", "name_te", "state", "lgd_code", "is_active")
    list_filter = ("is_active", "state")
    list_select_related = ("state",)
    autocomplete_fields = ("state",)


@admin.register(Mandal)
class MandalAdmin(AreaAdmin):
    list_display = ("name_en", "name_te", "district", "lgd_code", "is_active")
    list_filter = ("is_active", "district__state", "district")
    list_select_related = ("district",)
    autocomplete_fields = ("district",)


class HasCoordinatesFilter(admin.SimpleListFilter):
    title = "coordinates"
    parameter_name = "coordinates"

    def lookups(self, request, model_admin):
        return [("yes", "Has coordinates"), ("no", "Missing coordinates")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.with_coordinates()
        if self.value() == "no":
            return queryset.filter(latitude__isnull=True) | queryset.filter(longitude__isnull=True)
        return queryset


@admin.register(Village)
class VillageAdmin(AreaAdmin):
    list_display = ("name_en", "name_te", "mandal", "pin_code", "latitude", "longitude", "lgd_code", "is_active")
    list_filter = ("is_active", HasCoordinatesFilter, "mandal__district")
    list_select_related = ("mandal",)
    autocomplete_fields = ("mandal",)
    search_fields = ("name_en", "name_te", "lgd_code", "pin_code")
